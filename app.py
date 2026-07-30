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

for d in [DATA_DIR, LURES_DIR, CATCHES_DIR]:
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
    
    is_admin = make_admin or (len(users) == 0) # First registered user becomes admin automatically

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
    return load_user_data("catches")

def save_catches(catches):
    save_user_data("catches", catches)

def load_lures():
    return load_user_data("lures")

def save_lures(lures):
    save_user_data("lures", lures)


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
tab_names = ["🎣 Log a Catch", "🗺️ Catch Map", "🧩 Manage Lures", "📊 History & Analytics"]
if user.get("is_admin"):
    tab_names.append("🛡️ User Management")

tabs = st.tabs(tab_names)
tab1, tab2, tab3, tab4 = tabs[0], tabs[1], tabs[2], tabs[3]
admin_tab = tabs[4] if user.get("is_admin") else None


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


def extract_exif(image_file):
    try:
        image = Image.open(image_file)
        exif_data = image._getexif()
        if not exif_data:
            return datetime.now(), None, None
        return datetime.now(), None, None
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
    upload_method = st.radio("Input Method", ["Gallery Upload", "Camera"], horizontal=True, key="upload_method_radio")
    catch_image_file = st.camera_input("Take photo", key="cam_input") if upload_method == "Camera" else st.file_uploader("Upload photo", type=["jpg", "jpeg", "png"], key="file_input")

    if catch_image_file:
        rotation = st.selectbox("Rotate Image", [0, 90, 180, 270], format_func=lambda x: f"Rotate {x}°", key="rot_sel")
        processed_image = process_image_orientation(catch_image_file, rotation)
        st.image(processed_image, caption="Processed Photo", width=350)

        dt, lat, lon = extract_exif(catch_image_file)
        col1, col2 = st.columns(2)
        with col1:
            log_date = st.date_input("Date", value=dt.date(), key="c_date")
            log_time = st.time_input("Time", value=dt.time(), key="c_time")
        with col2:
            manual_lat = st.number_input("Latitude", value=28.39, format="%.6f", key="c_lat")
            manual_lon = st.number_input("Longitude", value=-80.60, format="%.6f", key="c_lon")

        combined_dt = datetime.combine(log_date, log_time)
        formatted_dt_str = combined_dt.strftime("%m/%d/%Y %I:%M %p")
        
        weather_desc, wind_speed, wind_dir = get_nws_weather(manual_lat, manual_lon)
        st.info(f"🌤️ **Weather:** {weather_desc} | 💨 **Wind:** {wind_speed} {wind_dir} | 🌊 **Tide:** {get_tide_info(manual_lat, manual_lon, combined_dt)} | 🌙 **Moon:** {get_moon_phase(combined_dt)}")

        lures = load_lures()
        rec_species, rec_lure = recognize_fish_and_lure(catch_image_file, lures)

        species = st.text_input("Fish Species", value=rec_species, key="c_species")
        length = st.slider("Length (Inches)", 0.0, 40.0, 15.0, 0.5, key="c_len")
        selected_lure = st.selectbox("Lure Used", [l["name"] for l in lures] if lures else ["None"], key="c_lure")

        if st.button("Save Catch Entry", type="primary"):
            img_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
            img_path = os.path.join(CATCHES_DIR, img_filename)
            processed_image.save(img_path, optimize=True, quality=80)

            catches = load_catches()
            catches.append({
                "id": str(uuid.uuid4()),
                "date": log_date.strftime("%Y-%m-%d"),
                "time": log_time.strftime("%H:%M:%S"),
                "formatted_datetime": formatted_dt_str,
                "latitude": manual_lat,
                "longitude": manual_lon,
                "species": species,
                "length": length,
                "lure": selected_lure,
                "weather": weather_desc,
                "wind_speed": wind_speed,
                "wind_direction": wind_dir,
                "tide": get_tide_info(manual_lat, manual_lon, combined_dt),
                "moon_phase": get_moon_phase(combined_dt),
                "image_path": img_path
            })
            save_catches(catches)
            st.success("Catch successfully logged!")
            st.rerun()


# --- TAB 2: CATCH MAP ---
with tab2:
    st.header("Catch Location Map")
    catches = load_catches()
    if catches:
        filtered_df = get_filtered_catches_df(catches, prefix="map_tab")
        valid = [r.to_dict() for _, r in filtered_df.iterrows() if r.get("latitude") is not None and r.get("longitude") is not None]
        if valid:
            m = folium.Map(location=[float(valid[0]["latitude"]), float(valid[0]["longitude"])], zoom_start=11)
            for c in valid:
                folium.Marker(location=[float(c["latitude"]), float(c["longitude"])], popup=f"{c['species']} ({c['length']} in)").add_to(m)
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
        for _, row in filtered_df.iterrows():
            st.write(f"🐟 **{row.get('species')}** - {row.get('length')} inches on {row.get('formatted_datetime')}")
    else:
        st.info("No history found.")


# --- TAB 5: ADMIN MANAGEMENT CONSOLE ---
if admin_tab:
    with admin_tab:
        st.header("🛡️ User Management Console")
        st.write("List of all registered users in the system:")
        all_users = get_all_users()
        if all_users:
            users_df = pd.DataFrame(all_users)[["first_name", "last_name", "email", "zip_code", "is_admin"]]
            st.dataframe(users_df, use_container_width=True)
        else:
            st.info("No users registered.")
