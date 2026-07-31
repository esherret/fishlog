import os
import uuid
import bcrypt
from datetime import datetime, time
import pandas as pd
import requests
import streamlit as st
from streamlit_folium import st_folium
import folium
from supabase import create_client, Client
from PIL import Image, ImageOps
from streamlit_cookies_controller import CookieController

# Configure page
st.set_page_config(page_title="Fish Catch Log", page_icon="🎣", layout="wide")

# Initialize persistent cookie controller
controller = CookieController()

# Initialize form version counter in session state for instant clearing
if "form_version" not in st.session_state:
    st.session_state.form_version = 0

# Custom CSS for black slider thumbs, tracks, sidebar filter icon, and responsive mobile lure grid
st.markdown("""
<style>
    .stSlider [data-baseweb="slider"] div[role="slider"] {
        background-color: #000000 !important;
        border-color: #000000 !important;
    }
    .stSlider [data-baseweb="slider"] div > div > div > div {
        background-color: #000000 !important;
    }
    [data-testid="collapsedControl"] svg {
        visibility: hidden;
    }
    [data-testid="collapsedControl"]::after {
        content: "🔍";
        font-size: 18px;
        display: flex;
        align-items: center;
        justify-content: center;
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
    }
    @media (max-width: 768px) {
        div[data-testid="stHorizontalBlock"] {
            display: flex !important;
            flex-wrap: wrap !important;
        }
        div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
            flex: 0 0 50% !important;
            max-width: 50% !important;
            min-width: 50% !important;
        }
    }
</style>
""", unsafe_allow_html=True)

DATA_DIR = "data"
LURES_DIR = os.path.join(DATA_DIR, "lures")
CATCHES_DIR = os.path.join(DATA_DIR, "catches")
SPECIES_SAMPLES_DIR = os.path.join(DATA_DIR, "species_samples")

for d in [DATA_DIR, LURES_DIR, CATCHES_DIR, SPECIES_SAMPLES_DIR]:
    os.makedirs(d, exist_ok=True)


def get_supabase_client() -> Client:
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        return create_client(url, key)
    except Exception as e:
        st.error(f"Failed to connect to Supabase via secrets: {e}")
        return None


# --- AUTHENTICATION & USER MANAGEMENT ---
def get_all_users():
    client = get_supabase_client()
    if not client:
        return []
    try:
        res = client.table("users").select("*").execute()
        return res.data if res.data else []
    except Exception:
        return []


def register_user(first_name, last_name, email, password, zip_code, make_admin=False):
    client = get_supabase_client()
    if not client:
        return False, "Database connection error."
    
    users = get_all_users()
    if any(u["email"].lower() == email.lower() for u in users):
        return False, "An account with this email already exists."

    user_id = str(uuid.uuid4())
    salt = bcrypt.gensalt()
    pwd_hash = bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")
    is_admin = make_admin or (len(users) == 0)

    try:
        client.table("users").insert({
            "id": user_id,
            "first_name": first_name,
            "last_name": last_name,
            "email": email.lower(),
            "password_hash": pwd_hash,
            "zip_code": zip_code,
            "is_admin": is_admin
        }).execute()
        return True, user_id
    except Exception as e:
        return False, str(e)


def authenticate_user(email, password):
    client = get_supabase_client()
    if not client:
        return None
    try:
        res = client.table("users").select("*").eq("email", email.lower()).execute()
        if res.data and len(res.data) > 0:
            user = res.data[0]
            if bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8")):
                return user
    except Exception:
        pass
    return None


def admin_update_user(user_id, first_name, last_name, email, zip_code, is_admin):
    client = get_supabase_client()
    if not client:
        return False, "Database connection error."
    try:
        client.table("users").update({
            "first_name": first_name,
            "last_name": last_name,
            "email": email.lower(),
            "zip_code": zip_code,
            "is_admin": is_admin
        }).eq("id", user_id).execute()
        return True, "User updated successfully."
    except Exception as e:
        return False, str(e)


def admin_delete_user(user_id):
    client = get_supabase_client()
    if not client:
        return False, "Database connection error."
    try:
        client.table("catches").delete().eq("user_id", user_id).execute()
        client.table("lures").delete().eq("user_id", user_id).execute()
        client.table("species_samples").delete().eq("user_id", user_id).execute()
        client.table("users").delete().eq("id", user_id).execute()
        return True, "User deleted successfully."
    except Exception as e:
        return False, str(e)


# Persistent Cookie Check for Auto-Login
if "current_user" not in st.session_state:
    st.session_state.current_user = None

cookie_user_id = controller.get("fishlog_user_id")
if cookie_user_id and not st.session_state.current_user:
    client = get_supabase_client()
    if client:
        try:
            res = client.table("users").select("*").eq("id", cookie_user_id).execute()
            if res.data:
                st.session_state.current_user = res.data[0]
        except Exception:
            pass


# --- DATA LOAD / SAVE HELPERS (USER ISOLATED) ---
def load_user_data(table_name):
    client = get_supabase_client()
    user = st.session_state.current_user
    if not client or not user:
        return []
    try:
        res = client.table(table_name).select("*").eq("user_id", user["id"]).execute()
        return res.data if res.data else []
    except Exception:
        return []


def save_user_data(table_name, data_list):
    client = get_supabase_client()
    user = st.session_state.current_user
    if not client or not user:
        return
    try:
        client.table(table_name).delete().eq("user_id", user["id"]).execute()
        if data_list:
            cleaned_data = []
            for item in data_list:
                row = {str(k): (str(v) if v is not None else "") for k, v in item.items()}
                row["user_id"] = user["id"]
                if "id" not in row or not row["id"]:
                    row["id"] = str(uuid.uuid4())
                cleaned_data.append(row)
            client.table(table_name).insert(cleaned_data).execute()
    except Exception as e:
        st.error(f"Error saving data: {e}")


def load_catches():
    all_data = load_user_data("catches")
    return [c for c in all_data if str(c.get("is_deleted", "false")).lower() != "true"]

def load_trashed_catches():
    all_data = load_user_data("catches")
    return [c for c in all_data if str(c.get("is_deleted", "false")).lower() == "true"]

def load_all_catches_raw():
    return load_user_data("catches")

def save_catches(catches):
    existing_raw = load_all_catches_raw()
    deleted_items = [c for c in existing_raw if str(c.get("is_deleted", "false")).lower() == "true"]
    combined = catches + deleted_items
    save_user_data("catches", combined)

def save_all_catches_raw(catches_list):
    save_user_data("catches", catches_list)

def load_lures():
    return load_user_data("lures")

def save_lures(lures):
    save_user_data("lures", lures)


# Global Species Sample Reference Library (Shared across users for recognition accuracy)
def load_species_samples():
    client = get_supabase_client()
    if not client:
        return []
    try:
        res = client.table("species_samples").select("*").execute()
        return res.data if res.data else []
    except Exception:
        return []

def save_species_samples_table(samples_list):
    client = get_supabase_client()
    if not client:
        return
    try:
        client.table("species_samples").delete().neq("id", "0").execute()
        if samples_list:
            client.table("species_samples").insert(samples_list).execute()
    except Exception as e:
        st.error(f"Error saving species samples: {e}")


# --- LOGIN / REGISTRATION UI ---
if not st.session_state.current_user:
    st.title("🎣 Fish Catch Log - Login")
    auth_tab1, auth_tab2 = st.tabs(["🔑 Sign In", "📝 Create Account"])

    with auth_tab1:
        with st.form("login_form"):
            login_email = st.text_input("Email Address")
            login_password = st.text_input("Password", type="password")
            remember_me = st.checkbox("Remember Me (Stay Logged In)", value=True)
            submit_login = st.form_submit_button("Sign In", type="primary")

            if submit_login:
                user = authenticate_user(login_email, login_password)
                if user:
                    st.session_state.current_user = user
                    if remember_me:
                        controller.set("fishlog_user_id", user["id"], max_age=31536000)
                    st.success(f"Welcome back, {user['first_name']}!")
                    st.rerun()
                else:
                    st.error("Invalid email or password.")

    with auth_tab2:
        with st.form("register_form"):
            reg_first = st.text_input("First Name")
            reg_last = st.text_input("Last Name")
            reg_email = st.text_input("Email Address")
            reg_pass = st.text_input("Create Password", type="password")
            reg_zip = st.text_input("Home Zip Code")
            submit_reg = st.form_submit_button("Register Profile", type="primary")

            if submit_reg:
                if reg_first and reg_last and reg_email and reg_pass and reg_zip:
                    success, result = register_user(reg_first, reg_last, reg_email, reg_pass, reg_zip)
                    if success:
                        all_users_check = get_all_users()
                        new_user = next((u for u in all_users_check if u["id"] == result), None)
                        st.session_state.current_user = new_user
                        controller.set("fishlog_user_id", result, max_age=31536000)
                        st.success("Account created successfully!")
                        st.rerun()
                    else:
                        st.error(f"Registration failed: {result}")
                else:
                    st.error("Please fill out all fields.")
    st.stop()


# --- LOGGED IN USER APP INTERFACE ---
user = st.session_state.current_user

st.sidebar.write(f"👤 **Logged in as:** {user['first_name']} {user['last_name']}")
if st.sidebar.button("🚪 Sign Out"):
    controller.set("fishlog_user_id", "", max_age=0)
    st.session_state.current_user = None
    st.rerun()

# Build navigation tabs dynamically based on Admin role
tab_names = ["🎣 Log a Catch", "🗺️ Catch Map", "🧩 Manage Lures", "📊 History & Analytics", "🗑️ Recycle Bin"]
if user.get("is_admin"):
    tab_names.append("🛡️ User Management")
    tab_names.append("🧬 Fish Recognition Library")

tabs = st.tabs(tab_names)
tab1, tab2, tab3, tab4, tab5 = tabs[0], tabs[1], tabs[2], tabs[3], tabs[4]
admin_tab = tabs[5] if user.get("is_admin") and len(tabs) > 5 else None
recognition_tab = tabs[6] if user.get("is_admin") and len(tabs) > 6 else None


# Helper functions for app processing
def process_image_orientation(image_file, rotation_angle=0):
    try:
        image = Image.open(image_file)
        image = ImageOps.exif_transpose(image)
    except Exception:
        image = Image.open(image_file)
    if image.mode in ("RGBA", "P"):
        image = image.convert("RGB")
    image.thumbnail((800, 800))
    if rotation_angle != 0:
        image = image.rotate(rotation_angle, expand=True)
    return image


def _convert_to_degrees(value):
    try:
        d = float(value[0])
        m = float(value[1])
        s = float(value[2])
        return d + (m / 60.0) + (s / 3600.0)
    except Exception:
        return float(value)


def extract_exif(image_file):
    try:
        image = Image.open(image_file)
        exif_data = image._getexif()
        if not exif_data:
            return datetime.now(), None, None

        dt_str = exif_data.get(36867) or exif_data.get(306)
        dt = datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S") if dt_str else datetime.now()

        lat, lon = None, None
        gps_info = exif_data.get(34853)
        if gps_info:
            lat_data = gps_info.get(2)
            lat_ref = gps_info.get(1)
            lon_data = gps_info.get(4)
            lon_ref = gps_info.get(3)

            if lat_data and lat_ref and lon_data and lon_ref:
                lat = _convert_to_degrees(lat_data)
                if str(lat_ref).upper() != "N":
                    lat = -lat
                lon = _convert_to_degrees(lon_data)
                if str(lon_ref).upper() != "E":
                    lon = -lon

        return dt, lat, lon
    except Exception:
        return datetime.now(), None, None


def get_nws_weather(lat, lon):
    if not lat or not lon:
        return "Location not available", "N/A", "N/A"
    try:
        points_url = f"https://api.weather.gov/points/{lat},{lon}"
        res = requests.get(points_url, headers={"User-Agent": "(fishapp, test@example.com)"}, timeout=4)
        if res.status_code != 200:
            return "Weather unavailable", "N/A", "N/A"
        forecast_url = res.json()["properties"]["forecastHourly"]
        forecast_res = requests.get(forecast_url, headers={"User-Agent": "(fishapp, test@example.com)"}, timeout=4)
        if forecast_res.status_code != 200:
            return "Weather unavailable", "N/A", "N/A"
        period = forecast_res.json()["properties"]["periods"][0]
        temp = f"{period.get('temperature')} {period.get('temperatureUnit')}"
        return f"{period.get('shortForecast')}, Temp: {temp}", period.get('windSpeed', '0 mph'), period.get('windDirection', 'N/A')
    except Exception:
        return "Error fetching weather", "N/A", "N/A"


def get_tide_info(lat, lon, dt):
    hour_offset = (dt.hour + dt.minute / 60.0) % 12.42
    if hour_offset < 3.1:
        return "Incoming (Rising)"
    elif hour_offset < 6.2:
        return "High Tide"
    elif hour_offset < 9.3:
        return "Outgoing (Falling)"
    else:
        return "Low Tide"


def get_moon_phase(dt):
    known_new_moon = datetime(2000, 1, 6, 18, 14)
    diff = dt - known_new_moon
    days = diff.total_seconds() / 86400
    phase = (days % 29.53058867) / 29.53058867
    if phase < 0.03 or phase > 0.97: return "New Moon"
    elif phase < 0.22: return "Waxing Crescent"
    elif phase < 0.28: return "First Quarter"
    elif phase < 0.47: return "Waxing Gibbous"
    elif phase < 0.53: return "Full Moon"
    elif phase < 0.72: return "Waning Gibbous"
    elif phase < 0.78: return "Third Quarter"
    else: return "Waning Crescent"


def recognize_fish_and_lure(image_file, lures):
    try:
        img = Image.open(image_file).convert('RGB')
        img_resized = img.resize((30, 30))
        target_pixels = list(img_resized.getdata())
        target_r = sum(p[0] for p in target_pixels) / len(target_pixels)
        target_g = sum(p[1] for p in target_pixels) / len(target_pixels)
        target_b = sum(p[2] for p in target_pixels) / len(target_pixels)

        samples = load_species_samples()
        if samples:
            best_match = None
            min_diff = float('inf')
            for sample in samples:
                sample_path = sample.get("image_path")
                if sample_path and os.path.exists(sample_path):
                    try:
                        s_img = Image.open(sample_path).convert('RGB').resize((30, 30))
                        s_pixels = list(s_img.getdata())
                        s_r = sum(p[0] for p in s_pixels) / len(s_pixels)
                        s_g = sum(p[1] for p in s_pixels) / len(s_pixels)
                        s_b = sum(p[2] for p in s_pixels) / len(s_pixels)
                        
                        diff = abs(target_r - s_r) + abs(target_g - s_g) + abs(target_b - s_b)
                        if diff < min_diff:
                            min_diff = diff
                            best_match = sample.get("species")
                    except Exception:
                        continue
            if best_match:
                detected_species = best_match
            else:
                detected_species = "Snook"
        else:
            detected_species = "Snook"
    except Exception:
        detected_species = "Snook"

    detected_lure = lures[0]["name"] if lures else None
    return detected_species, detected_lure


def get_filtered_catches_df(catches, prefix="global"):
    if not catches:
        return pd.DataFrame()
    df = pd.DataFrame(catches)
    if "length" in df.columns:
        df["length"] = pd.to_numeric(df["length"], errors="coerce").fillna(0.0)
    if "latitude" in df.columns:
        df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    if "longitude" in df.columns:
        df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")

    st.sidebar.header("Filter Log Entries")
    species_list = ["All"] + list(df["species"].unique()) if "species" in df.columns else ["All"]
    selected_species = st.sidebar.selectbox("Species", species_list, key=f"{prefix}_species_sb")
    min_size = st.sidebar.slider("Minimum Size (Inches)", 0.0, 40.0, 0.0, 0.5, key=f"{prefix}_size_slider")
    
    filtered_df = df.copy()
    if selected_species != "All":
        filtered_df = filtered_df[filtered_df["species"] == selected_species]
    filtered_df = filtered_df[filtered_df["length"] >= min_size]
    return filtered_df


# --- TAB 1: LOG A CATCH ---
with tab1:
    st.header("Log a New Catch")
    
    # Use dynamic version suffix to force-clear all input widgets upon submission
    v = st.session_state.form_version
    
    upload_method = st.radio("Input Method", ["Gallery Upload", "Camera"], horizontal=True, key=f"upload_method_radio_{v}")
    
    if upload_method == "Camera":
        catch_image_file = st.camera_input("Take photo", key=f"cam_input_widget_{v}")
    else:
        catch_image_file = st.file_uploader("Upload photo", type=["jpg", "jpeg", "png"], key=f"file_input_widget_{v}")

    if catch_image_file:
        rotation = st.selectbox("Rotate Image", [0, 90, 180, 270], format_func=lambda x: f"Rotate {x}°", key=f"rot_sel_{v}")
        processed_image = process_image_orientation(catch_image_file, rotation)
        st.image(processed_image, caption="Processed Photo", width=350)

        dt, lat, lon = extract_exif(catch_image_file)
        
        col_dt1, col_dt2 = st.columns(2)
        with col_dt1:
            log_date = st.date_input("Date", value=dt.date() if dt else datetime.now().date(), format="MM/DD/YYYY", key=f"c_date_{v}")
        with col_dt2:
            log_time = st.time_input("Time", value=dt.time() if dt else datetime.now().time(), key=f"c_time_{v}")

        st.write("📍 **Catch Location:**")
        col_lat, col_lon = st.columns(2)
        with col_lat:
            manual_lat = st.number_input("Latitude", value=lat if lat is not None else 28.39, format="%.6f", key=f"c_lat_{v}")
        with col_lon:
            manual_lon = st.number_input("Longitude", value=lon if lon is not None else -80.60, format="%.6f", key=f"catch_lon_input_{v}")
        
        m_thumb = folium.Map(location=[manual_lat, manual_lon], zoom_start=13, width="100%", height="250px", tiles="Esri.WorldImagery", attribution_control=False)
        fish_icon = folium.Icon(icon="fish", prefix="fa", color="blue", icon_color="white")
        folium.Marker(location=[manual_lat, manual_lon], popup="Catch Location", icon=fish_icon).add_to(m_thumb)
        st_folium(m_thumb, width=700, height=250, key=f"thumb_map_{v}")

        combined_dt = datetime.combine(log_date, log_time)
        formatted_dt_str = combined_dt.strftime("%m/%d/%Y %I:%M %p")
        
        weather_desc, wind_speed, wind_dir = get_nws_weather(manual_lat, manual_lon)
        st.info(f"🌤️ **Weather:** {weather_desc} | 💨 **Wind:** {wind_speed} {wind_dir} | 🌊 **Tide:** {get_tide_info(manual_lat, manual_lon, combined_dt)} | 🌙 **Moon:** {get_moon_phase(combined_dt)}")

        lures = load_lures()
        rec_species, rec_lure = recognize_fish_and_lure(catch_image_file, lures)

        samples = load_species_samples()
        known_species = sorted(list(set([s.get("species") for s in samples if s.get("species")])))
        if not known_species:
            known_species = ["Snook", "Redfish", "Trout", "Tarpon", "Bass", "Flounder"]
        if rec_species not in known_species:
            known_species.insert(0, rec_species)

        col_sp1, col_sp2 = st.columns([3, 1])
        with col_sp1:
            is_correct_id = st.checkbox("Correctly ID'd", value=False, key=f"correct_id_check_{v}")
        
        if is_correct_id:
            species = st.text_input("Type of Fish", value=rec_species, key=f"c_species_{v}")
        else:
            species = st.selectbox("Select Type of Fish", known_species, key=f"c_species_sb_{v}")

        length = st.slider("Length (Inches)", 0.0, 40.0, 15.0, 0.5, key=f"c_len_{v}")
        selected_lure = st.selectbox("Lure Used", [l["name"] for l in lures] if lures else ["None"], key=f"c_lure_{v}")

        if st.button("Save Catch Entry", type="primary", key=f"save_btn_{v}"):
            img_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
            img_path = os.path.join(CATCHES_DIR, img_filename)
            processed_image.save(img_path, optimize=True, quality=80)

            catch_id = str(uuid.uuid4())
            catches = load_all_catches_raw()
            catches.append({
                "id": catch_id,
                "user_id": user["id"],
                "date": log_date.strftime("%m/%d/%Y"),
                "time": log_time.strftime("%I:%M %p"),
                "formatted_datetime": formatted_dt_str,
                "latitude": manual_lat,
                "longitude": manual_lon,
                "species": species if species else "Unknown",
                "length": length,
                "lure": selected_lure,
                "weather": weather_desc,
                "wind_speed": wind_speed,
                "wind_direction": wind_dir,
                "tide": get_tide_info(manual_lat, manual_lon, combined_dt),
                "moon_phase": get_moon_phase(combined_dt),
                "image_path": img_path,
                "is_deleted": "false"
            })
            save_all_catches_raw(catches)

            if is_correct_id:
                all_samples = load_species_samples()
                all_samples.append({
                    "id": str(uuid.uuid4()),
                    "user_id": user["id"],
                    "catch_id": catch_id,
                    "species": species if species else "Unknown",
                    "image_path": img_path
                })
                save_species_samples_table(all_samples)

            st.success("Catch successfully logged!")
            
            # Increment form version to completely destroy and re-initialize all input widgets instantly
            st.session_state.form_version += 1
            st.rerun()
    else:
        st.info("Please upload or take a photo of your catch to begin logging.")


# --- TAB 2: CATCH MAP ---
with tab2:
    st.header("Catch Location Map")
    catches = load_catches()
    if catches:
        filtered_df = get_filtered_catches_df(catches, prefix="map_tab")
        valid = [r.to_dict() for _, r in filtered_df.iterrows() if r.get("latitude") is not None and r.get("longitude") is not None]
        if valid:
            m = folium.Map(location=[float(valid[0]["latitude"]), float(valid[0]["longitude"])], zoom_start=11, tiles="Esri.WorldImagery", attribution_control=False)
            fish_icon = folium.Icon(icon="fish", prefix="fa", color="blue", icon_color="white")
            for c in valid:
                img_path = c.get("image_path")
                img_html = ""
                if img_path and os.path.exists(img_path):
                    import base64
                    with open(img_path, "rb") as img_file:
                        encoded = base64.b64encode(img_file.read()).decode("utf-8")
                        img_html = f"<br><img src='data:image/jpeg;base64,{encoded}' width='150' style='border-radius: 4px; margin-top: 5px;'/>"
                
                popup_html = f"""
                <div style="font-family: sans-serif; width: 160px;">
                    <b>🐟 {c.get('species')}</b><br>
                    <b>Length:</b> {c.get('length')} in<br>
                    <b>Date:</b> {c.get('formatted_datetime')}<br>
                    <b>Lure:</b> {c.get('lure')}<br>
                    {img_html}
                </div>
                """
                folium.Marker(
                    location=[float(c["latitude"]), float(c["longitude"])],
                    popup=folium.Popup(popup_html, max_width=200),
                    icon=fish_icon
                ).add_to(m)
            st_folium(m, width=700, height=500)
        else:
            st.info("No mapped catches match filters.")
    else:
        st.info("No catches recorded yet.")


# --- TAB 3: MANAGE LURES ---
with tab3:
    st.header("Manage Lures")
    with st.form("lure_form", clear_on_submit=True):
        l_name = st.text_input("New Lure Name")
        l_img = st.file_uploader("Upload Lure Image", type=["jpg", "jpeg", "png"])
        if st.form_submit_button("Add Lure") and l_name and l_img:
            img_path = os.path.join(LURES_DIR, f"lure_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
            process_image_orientation(l_img).save(img_path, optimize=True, quality=80)
            lures = load_lures()
            lures.append({"id": str(uuid.uuid4()), "name": l_name, "image_path": img_path})
            save_lures(lures)
            st.success("Lure added!")
            st.rerun()

    lures = load_lures()
    for lure in lures:
        st.write(f"🎣 {lure['name']}")


# --- TAB 4: HISTORY & ANALYTICS ---
with tab4:
    st.header("Catch History")
    catches = load_catches()
    if catches:
        filtered_df = get_filtered_catches_df(catches, prefix="hist_tab")
        
        view_style = st.radio("History View Style", ["Card View", "List View"], horizontal=True, key="history_view_style_radio")

        if view_style == "List View":
            st.write("List View of your logged catches:")
            
            df_display = filtered_df.copy()
            df_display.insert(0, "Select", False)
            
            display_cols = ["Select", "formatted_datetime", "species", "length", "lure", "weather", "wind_speed", "wind_direction", "tide", "moon_phase"]
            available_cols = [c for c in display_cols if c in df_display.columns]
            
            edited_df = st.data_editor(
                df_display[available_cols],
                column_config={
                    "Select": st.column_config.CheckboxColumn("Select", help="Select records to move to Recycle Bin", default=False),
                    "formatted_datetime": "Date/Time",
                    "species": "Type of Fish",
                    "length": "Length (in)",
                    "lure": "Lure",
                    "weather": "Weather",
                    "wind_speed": "Wind Speed",
                    "wind_direction": "Wind Dir",
                    "tide": "Tide",
                    "moon_phase": "Moon"
                },
                disabled=[c for c in available_cols if c != "Select"],
                hide_index=True,
                width='stretch',
                key="list_view_editor"
            )

            col_btn1, col_btn2 = st.columns([1, 4])
            with col_btn1:
                delete_selected_btn = st.button("🗑️ Delete Selected Catches", type="primary")

            if delete_selected_btn:
                selected_rows = edited_df[edited_df["Select"] == True]
                if not selected_rows.empty:
                    selected_datetime_strs = selected_rows["formatted_datetime"].tolist()
                    
                    all_raw = load_all_catches_raw()
                    deleted_ids = []
                    for c in all_raw:
                        dt_str = c.get("formatted_datetime") or f"{c.get('date')} {c.get('time')}"
                        if c.get("user_id") == user["id"] and dt_str in selected_datetime_strs and str(c.get("is_deleted", "false")).lower() != "true":
                            c["is_deleted"] = "true"
                            deleted_ids.append(c.get("id"))

                    save_all_catches_raw(all_raw)

                    samples = load_species_samples()
                    updated_samples = [s for s in samples if s.get("catch_id") not in deleted_ids]
                    save_species_samples_table(updated_samples)

                    st.success(f"Successfully moved {len(selected_rows)} selected catch(es) to Recycle Bin!")
                    st.rerun()
                else:
                    st.warning("No records selected for deletion.")

        else:
            for idx, row in filtered_df.iterrows():
                col_img, col_info, col_act = st.columns([1, 2, 1])
                with col_img:
                    img_p = row.get("image_path")
                    if img_p and os.path.exists(img_p):
                        st.image(img_p, width=150)
                        if st.button("🔍 Full Screen Image", key=f"fs_img_{idx}"):
                            st.session_state[f"fullscreen_{row.get('id')}"] = True

                        if st.session_state.get(f"fullscreen_{row.get('id')}", False):
                            st.markdown("---")
                            st.image(img_p, caption=f"{row.get('species')} - Full Screen", width='stretch')
                            if st.button("Close Full Screen", key=f"close_fs_{idx}"):
                                st.session_state[f"fullscreen_{row.get('id')}"] = False
                                st.rerun()
                            st.markdown("---")
                    
                    # Display smaller mini map matching image size below the image in Card View
                    lat_val = row.get("latitude")
                    lon_val = row.get("longitude")
                    if lat_val is not None and lon_val is not None:
                        try:
                            lat_f = float(lat_val)
                            lon_f = float(lon_val)
                            m_mini = folium.Map(
                                location=[lat_f, lon_f],
                                zoom_start=13,
                                width=150,
                                height=130,
                                tiles="Esri.WorldImagery",
                                zoom_control=False,
                                dragging=False,
                                scrollWheelZoom=False,
                                attribution_control=False
                            )
                            fish_icon_mini = folium.Icon(icon="fish", prefix="fa", color="blue", icon_color="white")
                            folium.Marker(location=[lat_f, lon_f], icon=fish_icon_mini).add_to(m_mini)
                            st_folium(m_mini, width=150, height=130, key=f"history_minimap_{idx}_{row.get('id')}")
                        except Exception:
                            pass

                with col_info:
                    st.write(f"🐟 **Type of Fish:** {row.get('species')} ({row.get('length')} in)")
                    st.write(f"📅 **Date/Time:** {row.get('formatted_datetime')}")
                    st.write(f"🎣 **Lure:** {row.get('lure')}")
                    st.write(f"🌤️ **Weather:** {row.get('weather')} | 💨 **Wind:** {row.get('wind_speed')} {row.get('wind_direction')}")
                    st.write(f"🌊 **Tide:** {row.get('tide')} | 🌙 **Moon:** {row.get('moon_phase')}")

                with col_act:
                    if st.button("Edit", key=f"edit_btn_{idx}"):
                        st.session_state[f"show_edit_panel_{row.get('id')}"] = True
                    if st.button("Delete", key=f"del_btn_{idx}"):
                        all_c = load_all_catches_raw()
                        for c in all_c:
                            if c.get("id") == row.get("id"):
                                c["is_deleted"] = "true"
                        save_all_catches_raw(all_c)
                        
                        samples = load_species_samples()
                        updated_samples = [s for s in samples if s.get("catch_id") != row.get("id")]
                        save_species_samples_table(updated_samples)
                        
                        st.success("Moved to Recycle Bin!")
                        st.rerun()

                # Interactive Edit expander panel
                if st.session_state.get(f"show_edit_panel_{row.get('id')}", False):
                    with st.form(key=f"edit_catch_form_{row.get('id')}"):
                        st.write(f"**Editing Catch ID:** {row.get('id')[:6]}")
                        new_species = st.text_input("Type of Fish", value=row.get("species", ""))
                        new_length = st.slider("Length (Inches)", 0.0, 40.0, float(row.get("length", 15.0)), 0.5, key=f"edit_len_{row.get('id')}")
                        new_lure = st.text_input("Lure", value=row.get("lure", ""))
                        new_img_file = st.file_uploader("Replace Catch Image (Optional)", type=["jpg", "jpeg", "png"], key=f"edit_file_{row.get('id')}")

                        col_sub_save, col_sub_cancel = st.columns(2)
                        with col_sub_save:
                            save_edits = st.form_submit_button("Save Changes", type="primary")
                        with col_sub_cancel:
                            cancel_edit = st.form_submit_button("Cancel")

                        if save_edits:
                            all_c = load_all_catches_raw()
                            for c in all_c:
                                if c.get("id") == row.get("id"):
                                    c["species"] = new_species
                                    c["length"] = new_length
                                    c["lure"] = new_lure
                                    if new_img_file:
                                        img_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
                                        img_path = os.path.join(CATCHES_DIR, img_filename)
                                        process_image_orientation(new_img_file).save(img_path, optimize=True, quality=80)
                                        c["image_path"] = img_path
                            save_all_catches_raw(all_c)
                            st.session_state[f"show_edit_panel_{row.get('id')}"] = False
                            st.success("Entry updated successfully!")
                            st.rerun()

                        if cancel_edit:
                            st.session_state[f"show_edit_panel_{row.get('id')}"] = False
                            st.rerun()

                st.divider()
    else:
        st.info("No history found.")


# --- TAB 5: RECYCLE BIN ---
with tab5:
    st.header("🗑️ Recycle Bin (Deleted Catches)")
    st.write("Restore catches deleted by mistake or permanently delete them.")
    
    trashed = load_trashed_catches()
    if trashed:
        for idx, row in enumerate(trashed):
            col_img, col_info, col_act1, col_act2 = st.columns([1, 2, 1, 1])
            with col_img:
                img_p = row.get("image_path")
                if img_p and os.path.exists(img_p):
                    st.image(img_p, width=120)
            with col_info:
                st.write(f"🐟 **{row.get('species')}** ({row.get('length')} in)")
                st.write(f"📅 **Date:** {row.get('formatted_datetime')}")
            with col_act1:
                if st.button("♻️ Restore", key=f"restore_{idx}_{row.get('id')}"):
                    all_raw = load_all_catches_raw()
                    for c in all_raw:
                        if c.get("id") == row.get("id"):
                            c["is_deleted"] = "false"
                    save_all_catches_raw(all_raw)
                    st.success("Catch restored!")
                    st.rerun()
            with col_act2:
                if st.button("🔥 Delete Forever", key=f"perm_del_{idx}_{row.get('id')}"):
                    all_raw = load_all_catches_raw()
                    updated_raw = [c for c in all_raw if c.get("id") != row.get("id")]
                    save_all_catches_raw(updated_raw)
                    st.success("Permanently deleted!")
                    st.rerun()
            st.divider()
    else:
        st.info("Recycle bin is empty.")


# --- TAB 6: ADMIN MANAGEMENT CONSOLE ---
if admin_tab:
    with admin_tab:
        st.header("🛡️ User Management Console")
        st.write("Manage registered user accounts, permissions, and records.")
        
        all_users = get_all_users()
        if all_users:
            for idx, u in enumerate(all_users):
                u_id = u["id"]
                u_name = f"{u['first_name']} {u['last_name']} ({u['email']})"
                
                with st.expander(f"👤 {u_name} {'[ADMIN]' if u['is_admin'] else ''}"):
                    with st.form(key=f"edit_user_form_{u_id}"):
                        col1, col2 = st.columns(2)
                        with col1:
                            edit_first = st.text_input("First Name", value=u["first_name"], key=f"ef_{u_id}")
                            edit_email = st.text_input("Email", value=u["email"], key=f"ee_{u_id}")
                        with col2:
                            edit_last = st.text_input("Last Name", value=u["last_name"], key=f"el_{u_id}")
                            edit_zip = st.text_input("Home Zip Code", value=u["zip_code"], key=f"ez_{u_id}")
                        
                        edit_is_admin = st.checkbox("Administrator Privileges", value=u["is_admin"], key=f"ea_{u_id}")
                        
                        col_save, col_del = st.columns(2)
                        with col_save:
                            submit_edit = st.form_submit_button("💾 Save Changes", type="primary")
                        with col_del:
                            submit_delete = st.form_submit_button("🗑️ Delete Account", type="secondary")

                        if submit_edit:
                            success, msg = admin_update_user(u_id, edit_first, edit_last, edit_email, edit_zip, edit_is_admin)
                            if success:
                                st.success(msg)
                                st.rerun()
                            else:
                                st.error(f"Error: {msg}")

                        if submit_delete:
                            if u_id == user["id"]:
                                st.error("You cannot delete your own active admin account while logged in.")
                            else:
                                success, msg = admin_delete_user(u_id)
                                if success:
                                    st.success(msg)
                                    st.rerun()
                                else:
                                    st.error(f"Error: {msg}")
        else:
            st.info("No users registered.")


# --- TAB 7: FISH RECOGNITION LIBRARY (ADMIN) ---
if recognition_tab:
    with recognition_tab:
        st.header("🧬 Fish Recognition Accuracy Library")
        st.write("Review, manage, and add reference sample images used by the heuristic model to improve species identification accuracy.")
        
        with st.form("add_recognition_sample_form", clear_on_submit=True):
            st.subheader("➕ Add New Recognition Reference Sample")
            new_sample_species = st.text_input("Fish Species Name")
            new_sample_file = st.file_uploader("Upload Reference Image", type=["jpg", "jpeg", "png"])
            submit_sample = st.form_submit_button("Add to Recognition Library", type="primary")
            
            if submit_sample:
                if new_sample_species and new_sample_file:
                    img_filename = f"sample_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
                    img_path = os.path.join(SPECIES_SAMPLES_DIR, img_filename)
                    process_image_orientation(new_sample_file).save(img_path, optimize=True, quality=80)
                    
                    samples = load_species_samples()
                    samples.append({
                        "id": str(uuid.uuid4()),
                        "user_id": user["id"],
                        "catch_id": "manual_admin_upload",
                        "species": new_sample_species,
                        "image_path": img_path
                    })
                    save_species_samples_table(samples)
                    st.success("New reference sample added successfully!")
                    st.rerun()
                else:
                    st.error("Please provide both a species name and an image file.")

        st.divider()
        st.subheader("Existing Reference Samples")
        samples = load_species_samples()
        if samples:
            for s_idx, sample in enumerate(samples):
                col_img, col_info, col_act = st.columns([1, 2, 1])
                with col_img:
                    if os.path.exists(sample.get("image_path", "")):
                        st.image(sample["image_path"], width=120)
                with col_info:
                    st.write(f"**Species:** {sample.get('species')}")
                    st.write(f"**Sample ID:** {sample.get('id')[:6]}")
                with col_act:
                    if st.button("Delete Reference", key=f"del_sample_{s_idx}"):
                        updated_samples = [s for s in samples if s.get("id") != sample.get("id")]
                        save_species_samples_table(updated_samples)
                        st.success("Reference sample removed!")
                        st.rerun()
                st.divider()
        else:
            st.info("No reference samples collected yet.")
