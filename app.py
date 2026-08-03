import os
import uuid
import re
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

# Custom CSS for sidebar filter icon, and responsive media layout
st.markdown("""
<style>
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
    /* Style the delete button red in card history action rows */
    div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-child(2) button {
        background-color: #ff4b4b !important;
        color: white !important;
        border-color: #ff4b4b !important;
    }
    div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-child(2) button:hover {
        background-color: #ff3333 !important;
        color: white !important;
    }
    /* Smartphone layout (< 768px): images take 90% width stacked */
    @media (max-width: 767px) {
        .card-media-block [data-testid="column"] {
            width: 90% !important;
            max-width: 90% !important;
            flex: 0 0 90% !important;
            margin: 0 auto 12px auto !important;
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

# --- HANDLE CLEAR FILTERS CALLBACK ---
if "clear_filters_flag" not in st.session_state:
    st.session_state.clear_filters_flag = False

if st.session_state.clear_filters_flag:
    st.session_state.sb_species = "All"
    st.session_state.sb_min_size = "All"
    st.session_state.sb_tide = "All"
    st.session_state.sb_min_wind_speed = "All"
    st.session_state.sb_wind_dir = "All"
    st.session_state.sb_lure = "All"
    st.session_state.clear_filters_flag = False

# --- CENTRALIZED SIDEBAR FILTERS (RENDERED ONCE) ---
st.sidebar.header("Filter Log Entries")
all_catches_for_filter = load_catches()

# Wind direction windows definition
wind_dir_windows = [
    ("N / NNE / NE", ["N", "NNE", "NE"]),
    ("NNE / NE / ENE", ["NNE", "NE", "ENE"]),
    ("NE / ENE / E", ["NE", "ENE", "E"]),
    ("ENE / E / ESE", ["ENE", "E", "ESE"]),
    ("E / ESE / SE", ["E", "ESE", "SE"]),
    ("ESE / SE / SSE", ["ESE", "SE", "SSE"]),
    ("SE / SSE / S", ["SE", "SSE", "S"]),
    ("SSE / S / SSW", ["SSE", "S", "SSW"]),
    ("S / SSW / SW", ["S", "SSW", "SW"]),
    ("SSW / SW / WSW", ["SSW", "SW", "WSW"]),
    ("SW / WSW / W", ["SW", "WSW", "W"]),
    ("WSW / W / WNW", ["WSW", "W", "WNW"]),
    ("W / WNW / NW", ["W", "WNW", "NW"]),
    ("WNW / NW / NNW", ["WNW", "NW", "NNW"]),
    ("NW / NNW / N", ["NW", "NNW", "N"]),
    ("NNW / N / NNE", ["NNW", "N", "NNE"])
]

if all_catches_for_filter:
    df_filter = pd.DataFrame(all_catches_for_filter)
    if "length" in df_filter.columns:
        df_filter["length"] = pd.to_numeric(df_filter["length"], errors="coerce").fillna(0.0)
    
    species_list = ["All"] + sorted(list(df_filter["species"].unique())) if "species" in df_filter.columns else ["All"]
    selected_species = st.sidebar.selectbox("Type of Fish", species_list, key="sb_species")
    
    size_options = ["All"] + [f"{x:.1f}" for x in [i * 0.5 for i in range(81)]]
    selected_min_size_str = st.sidebar.selectbox("Minimum Size (Inches)", size_options, key="sb_min_size")
    min_size = 0.0 if selected_min_size_str == "All" else float(selected_min_size_str)

    tide_list = ["All"] + sorted(list(df_filter["tide"].dropna().unique())) if "tide" in df_filter.columns else ["All"]
    selected_tide = st.sidebar.selectbox("Tide", tide_list, key="sb_tide")

    min_wind_speed_options = ["All"] + [f"{i}+ mph" for i in range(16)]
    selected_min_wind_speed = st.sidebar.selectbox("Minimum Wind Speed", min_wind_speed_options, key="sb_min_wind_speed")

    wind_dir_options = ["All"] + [w[0] for w in wind_dir_windows]
    selected_wind_dir_label = st.sidebar.selectbox("Wind Direction", wind_dir_options, key="sb_wind_dir")

    lure_list = ["All"] + sorted(list(df_filter["lure"].dropna().unique())) if "lure" in df_filter.columns else ["All"]
    selected_lure = st.sidebar.selectbox("Lure Used", lure_list, key="sb_lure")
else:
    selected_species = "All"
    min_size = 0.0
    selected_tide = "All"
    selected_min_wind_speed = "All"
    selected_wind_dir_label = "All"
    selected_lure = "All"

def parse_wind_speed(val):
    if not val or val == "N/A":
        return 0.0
    nums = re.findall(r'\d+', str(val))
    if nums:
        return float(nums[0])
    return 0.0

def get_filtered_catches_df():
    catches = load_catches()
    if not catches:
        return pd.DataFrame()
    df = pd.DataFrame(catches)
    if "length" in df.columns:
        df["length"] = pd.to_numeric(df["length"], errors="coerce").fillna(0.0)
    if "latitude" in df.columns:
        df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    if "longitude" in df.columns:
        df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    
    filtered_df = df.copy()
    if selected_species != "All":
        filtered_df = filtered_df[filtered_df["species"] == selected_species]
    filtered_df = filtered_df[filtered_df["length"] >= min_size]
    if selected_tide != "All":
        filtered_df = filtered_df[filtered_df["tide"] == selected_tide]
    
    if selected_min_wind_speed != "All":
        threshold = float(selected_min_wind_speed.replace("+ mph", ""))
        filtered_df = filtered_df[filtered_df["wind_speed"].apply(parse_wind_speed) >= threshold]

    if selected_wind_dir_label != "All":
        matched_window = next((w[1] for w in wind_dir_windows if w[0] == selected_wind_dir_label), [])
        filtered_df = filtered_df[filtered_df["wind_direction"].isin(matched_window)]

    if selected_lure != "All":
        filtered_df = filtered_df[filtered_df["lure"] == selected_lure]
        
    return filtered_df


# Build navigation tabs dynamically based on Admin role
tab_names = ["🎣 Log a Catch", "🗺️ Catch Map", "🧩 Manage Lures", "📋 Catch Log", "🗑️ Recycle Bin"]
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
        img_resized = img.resize((50, 50))
        pixels = list(img_resized.getdata())
        w, h = img_resized.size
        zones = [
            [pixels[y * w + x] for y in range(0, h//2) for x in range(0, w//2)],
            [pixels[y * w + x] for y in range(0, h//2) for x in range(w//2, w)],
            [pixels[y * w + x] for y in range(h//2, h) for x in range(0, w//2)],
            [pixels[y * w + x] for y in range(h//2, h) for x in range(w//2, w)],
        ]
        zone_avgs = []
        for zone in zones:
            if zone:
                r = sum(p[0] for p in zone) / len(zone)
                g = sum(p[1] for p in zone) / len(zone)
                b = sum(p[2] for p in zone) / len(zone)
                zone_avgs.extend([r, g, b])
            else:
                zone_avgs.extend([0, 0, 0])

        samples = load_species_samples()
        if samples:
            best_match = None
            min_diff = float('inf')
            for sample in samples:
                sample_path = sample.get("image_path")
                if sample_path and os.path.exists(sample_path):
                    try:
                        s_img = Image.open(sample_path).convert('RGB').resize((50, 50))
                        s_pixels = list(s_img.getdata())
                        s_w, s_h = s_img.size
                        s_zones = [
                            [s_pixels[y * s_w + x] for y in range(0, s_h//2) for x in range(0, s_w//2)],
                            [s_pixels[y * s_w + x] for y in range(0, s_h//2) for x in range(s_w//2, s_w)],
                            [s_pixels[y * s_w + x] for y in range(s_h//2, s_h) for x in range(0, s_w//2)],
                            [s_pixels[y * s_w + x] for y in range(s_h//2, s_h) for x in range(s_w//2, s_w)],
                        ]
                        s_zone_avgs = []
                        for s_zone in s_zones:
                            if s_zone:
                                sr = sum(p[0] for p in s_zone) / len(s_zone)
                                sg = sum(p[1] for p in s_zone) / len(s_zone)
                                sb = sum(p[2] for p in s_zone) / len(s_zone)
                                s_zone_avgs.extend([sr, sg, sb])
                            else:
                                s_zone_avgs.extend([0, 0, 0])

                        diff = sum(abs(a - b) for a, b in zip(zone_avgs, s_zone_avgs))
                        if diff < min_diff:
                            min_diff = diff
                            best_match = sample.get("species")
                    except Exception:
                        continue
            detected_species = best_match if best_match else "Snook"
        else:
            detected_species = "Snook"
    except Exception:
        detected_species = "Snook"

    detected_lure = lures[0]["name"] if lures else None
    return detected_species, detected_lure


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
        
        m_thumb = folium.Map(location=[manual_lat, manual_lon], zoom_start=12, width="100%", height="250px", tiles="Esri.WorldImagery", attribution_control=False)
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

        length_options = [f"{x:.1f}" for x in [i * 0.5 for i in range(81)]]
        default_len_idx = length_options.index("10.0") if "10.0" in length_options else 20
        selected_len_str = st.selectbox("Length (Inches)", length_options, index=default_len_idx, key=f"c_len_{v}")
        length = float(selected_len_str)
        
        # Lure Selection Dropdown + Quick Add Option
        lure_names = [l["name"] for l in lures] if lures else []
        lure_options = lure_names + ["➕ Add New Lure..."]
        selected_lure_choice = st.selectbox("Lure Used", lure_options, key=f"c_lure_sb_{v}")
        
        selected_lure = selected_lure_choice
        if selected_lure_choice == "➕ Add New Lure...":
            with st.expander("➕ Add New Lure", expanded=True):
                new_lure_name = st.text_input("New Lure Name", key=f"new_l_name_{v}")
                new_lure_img = st.file_uploader("Upload Lure Image", type=["jpg", "jpeg", "png"], key=f"new_l_img_{v}")
                if st.button("Save New Lure", key=f"save_new_lure_btn_{v}"):
                    if new_lure_name:
                        l_img_path = os.path.join(LURES_DIR, f"lure_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg")
                        if new_lure_img:
                            process_image_orientation(new_lure_img).save(l_img_path, optimize=True, quality=80)
                        else:
                            l_img_path = ""
                        
                        updated_lures = load_lures()
                        updated_lures.append({"id": str(uuid.uuid4()), "name": new_lure_name, "image_path": l_img_path})
                        save_lures(updated_lures)
                        st.success(f"Lure '{new_lure_name}' added successfully! Please re-select it from the dropdown.")
                        st.rerun()
                    else:
                        st.error("Please enter a lure name.")
            selected_lure = "None"

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

            # Add or update sample in Fish Recognition Library upon selection/saving
            all_samples = load_species_samples()
            existing_sample = next((s for s in all_samples if s.get("catch_id") == catch_id), None)
            if existing_sample:
                existing_sample["species"] = species if species else "Unknown"
            else:
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
        filtered_df = get_filtered_catches_df()
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
                    location=[float(c["latitude"], float(c["longitude"]))],
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
        if st.form_submit_button("Add Lure") and l_name:
            img_path = os.path.join(LURES_DIR, f"lure_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg")
            if l_img:
                process_image_orientation(l_img).save(img_path, optimize=True, quality=80)
            else:
                img_path = ""
            lures = load_lures()
            lures.append({"id": str(uuid.uuid4()), "name": l_name, "image_path": img_path})
            save_lures(lures)
            st.success("Lure added!")
            st.rerun()

    st.divider()
    st.subheader("Your Lures")
    lures = load_lures()
    if lures:
        for l_idx, lure in enumerate(lures):
            col_l_img, col_l_info, col_l_act = st.columns([1, 3, 1])
            with col_l_img:
                l_path = lure.get("image_path")
                if l_path and os.path.exists(l_path):
                    st.image(l_path, width=80)
            with col_l_info:
                st.write(f"🎣 **{lure.get('name')}**")
            with col_l_act:
                if st.button("Delete Lure", key=f"del_lure_{l_idx}_{lure.get('id')}"):
                    updated_lures = [l for l in lures if l.get("id") != lure.get("id")]
                    save_lures(updated_lures)
                    st.success("Lure deleted!")
                    st.rerun()
            st.divider()
    else:
        st.info("No lures added yet.")


# --- TAB 4: CATCH LOG ---
with tab4:
    st.header("Catch Log")
    catches = load_catches()
    if catches:
        filtered_df = get_filtered_catches_df()
        
        # Build active filter summary sentence
        filter_parts = []
        if selected_species != "All":
            filter_parts.append(f"species equal to **{selected_species}**")
        if min_size > 0.0:
            filter_parts.append(f"size of **{min_size:.1f} inches or greater**")
        if selected_tide != "All":
            filter_parts.append(f"tide condition of **{selected_tide}**")
        if selected_min_wind_speed != "All":
            filter_parts.append(f"wind speed of **{selected_min_wind_speed}**")
        if selected_wind_dir_label != "All":
            filter_parts.append(f"wind direction in the **{selected_wind_dir_label}** window")
        if selected_lure != "All":
            filter_parts.append(f"use of the **{selected_lure}** lure")

        if filter_parts:
            if len(filter_parts) == 1:
                filter_sentence = f"You are filtering your log on catches with {filter_parts[0]}."
            elif len(filter_parts) == 2:
                filter_sentence = f"You are filtering your log on catches with {filter_parts[0]} and {filter_parts[1]}."
            else:
                filter_sentence = f"You are filtering your log on catches with {', '.join(filter_parts[:-1])}, and {filter_parts[-1]}."
            
            st.markdown(f"*{filter_sentence}*")
            if st.button("Clear all filters"):
                st.session_state.clear_filters_flag = True
                st.rerun()
            st.divider()

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
            # Sort entries starting with newest and going to oldest based on combined datetime
            sorted_df = filtered_df.sort_values(by="formatted_datetime", ascending=False, key=lambda col: pd.to_datetime(filtered_df["formatted_datetime"], errors="coerce"))

            samples = load_species_samples()
            for idx, row in sorted_df.iterrows():
                st.markdown('<div class="card-media-block">', unsafe_allow_html=True)
                
                # 1. Text info displayed first at the top of the card in larger font with "19.0 in Snook" format
                st.markdown(f"### 🐟 {row.get('length')} in {row.get('species')} | 📅 {row.get('formatted_datetime')}")
                st.write(f"🌤️ {row.get('weather')} | 💨 {row.get('wind_speed')} {row.get('wind_direction')} | 🌊 {row.get('tide')} | 🌙 {row.get('moon_phase')}")

                # 2. Media row: 2 columns ([1, 1]) -> Left: Fish image, Right: Map on top, Lure name ABOVE picture underneath map
                media_col1, media_col2 = st.columns([1, 1])
                
                with media_col1:
                    img_p = row.get("image_path")
                    if img_p and os.path.exists(img_p):
                        st.image(img_p, use_container_width=True)

                with media_col2:
                    lat_val = row.get("latitude")
                    lon_val = row.get("longitude")
                    if lat_val is not None and lon_val is not None:
                        try:
                            lat_f = float(lat_val)
                            lon_f = float(lon_val)
                            m_mini = folium.Map(
                                location=[lat_f, lon_f],
                                zoom_start=12,
                                width="100%",
                                height=240,
                                tiles="Esri.WorldImagery",
                                zoom_control=True,
                                dragging=False,
                                scrollWheelZoom=False,
                                attribution_control=False
                            )
                            fish_icon_mini = folium.Icon(icon="fish", prefix="fa", color="blue", icon_color="white")
                            folium.Marker(location=[lat_f, lon_f], icon=fish_icon_mini).add_to(m_mini)
                            st_folium(m_mini, use_container_width=True, height=240, key=f"history_minimap_{idx}_{row.get('id')}")
                        except Exception:
                            pass

                    # Lure under the map with name ABOVE the picture (width=140)
                    lure_name = row.get("lure")
                    lures_list = load_lures()
                    matched_lure = next((l for l in lures_list if l.get("name", "").lower() == str(lure_name).lower()), None)
                    st.write("")
                    if matched_lure and matched_lure.get("image_path") and os.path.exists(matched_lure["image_path"]):
                        st.write(f"🎣 **{lure_name}**")
                        st.image(matched_lure["image_path"], width=140)
                    else:
                        st.write(f"🎣 **Lure:** {lure_name}")

                # 3. Edit and Delete buttons last at the bottom of the card (Delete button styled red)
                btn_col1, btn_col2 = st.columns([1, 1])
                with btn_col1:
                    if st.button("Edit", key=f"edit_btn_{idx}"):
                        st.session_state[f"show_edit_panel_{row.get('id')}"] = True
                with btn_col2:
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

                st.markdown('</div>', unsafe_allow_html=True)

                # Interactive Edit panel
                if st.session_state.get(f"show_edit_panel_{row.get('id')}", False):
                    with st.container():
                        st.write(f"**Editing Catch ID:** {row.get('id')[:6]}")
                        
                        # Species Dropdown & Quick Add in Edit Panel (Fixed line 981 syntax error)
                        known_species = sorted(list(set([s.get("species") for s in samples if s.get("species")] + [c.get("species") for c in catches if c.get("species")])))
                        if not known_species:
                            known_species = ["Snook", "Redfish", "Trout", "Tarpon", "Bass", "Flounder"]
                        curr_species = row.get("species", "")
                        if curr_species not in known_species:
                            known_species.insert(0, curr_species)
                        
                        species_options = known_species + ["➕ Add New Fish Type..."]
                        d_sp_idx = species_options.index(curr_species) if curr_species in species_options else 0
                        
                        selected_species_choice = st.selectbox("Type of Fish", species_options, index=d_sp_idx, key=f"edit_species_sb_{row.get('id')}")
                        
                        new_species = selected_species_choice
                        if selected_species_choice == "➕ Add New Fish Type...":
                            with st.expander("➕ Add New Fish Type", expanded=True):
                                new_fish_name = st.text_input("New Fish Type Name", key=f"edit_new_fish_name_{row.get('id')}")
                                if st.button("Save New Fish Type", key=f"edit_save_new_fish_btn_{row.get('id')}"):
                                    if new_fish_name:
                                        all_samples = load_species_samples()
                                        all_samples.append({
                                            "id": str(uuid.uuid4()),
                                            "user_id": user["id"],
                                            "catch_id": row.get("id"),
                                            "species": new_fish_name,
                                            "image_path": row.get("image_path", "")
                                        })
                                        save_species_samples_table(all_samples)
                                        st.success(f"Fish type '{new_fish_name}' added! Please re-select it.")
                                        st.rerun()
                                    else:
                                        st.error("Please enter a fish type name.")
                            new_species = curr_species

                        length_options = [f"{x:.1f}" for x in [i * 0.5 for i in range(81)]]
                        curr_len_str = f"{float(row.get('length', 10.0)):.1f}"
                        d_len_idx = length_options.index(curr_len_str) if curr_len_str in length_options else 20
                        selected_edit_len_str = st.selectbox("Length (Inches)", length_options, index=d_len_idx, key=f"edit_len_{row.get('id')}")
                        new_length = float(selected_edit_len_str)
                        
                        # Lure Dropdown & Quick Add in Edit Panel
                        lures_list = load_lures()
                        lure_names = [l["name"] for l in lures_list] if lures_list else []
                        lure_options = lure_names + ["➕ Add New Lure..."]
                        curr_lure = row.get("lure", "")
                        d_idx = lure_options.index(curr_lure) if curr_lure in lure_options else 0
                        
                        selected_lure_choice = st.selectbox("Lure Used", lure_options, index=d_idx, key=f"edit_lure_sb_{row.get('id')}")
                        
                        new_lure = selected_lure_choice
                        if selected_lure_choice == "➕ Add New Lure...":
                            with st.expander("➕ Add New Lure", expanded=True):
                                new_lure_name = st.text_input("New Lure Name", key=f"edit_new_l_name_{row.get('id')}")
                                new_lure_img = st.file_uploader("Upload Lure Image", type=["jpg", "jpeg", "png"], key=f"edit_new_l_img_{row.get('id')}")
                                if st.button("Save New Lure", key=f"edit_save_new_lure_btn_{row.get('id')}"):
                                    if new_lure_name:
                                        l_img_path = os.path.join(LURES_DIR, f"lure_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg")
                                        if new_lure_img:
                                            process_image_orientation(new_lure_img).save(l_img_path, optimize=True, quality=80)
                                        else:
                                            l_img_path = ""
                                        
                                        updated_lures = load_lures()
                                        updated_lures.append({"id": str(uuid.uuid4()), "name": new_lure_name, "image_path": l_img_path})
                                        save_lures(updated_lures)
                                        st.success(f"Lure '{new_lure_name}' added! Please re-select it.")
                                        st.rerun()
                                    else:
                                        st.error("Please enter a lure name.")
                            new_lure = "None"

                        new_img_file = st.file_uploader("Replace Catch Image (Optional)", type=["jpg", "jpeg", "png"], key=f"edit_file_{row.get('id')}")

                        col_sub_save, col_sub_cancel = st.columns(2)
                        with col_sub_save:
                            if st.button("Save Changes", type="primary", key=f"save_edit_btn_{row.get('id')}"):
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
                                            
                                            # Update recognition library sample
                                            samples = load_species_samples()
                                            s_match = next((s for s in samples if s.get("catch_id") == row.get("id")), None)
                                            if s_match:
                                                s_match["species"] = new_species
                                                s_match["image_path"] = img_path
                                            else:
                                                samples.append({
                                                    "id": str(uuid.uuid4()),
                                                    "user_id": user["id"],
                                                    "catch_id": row.get("id"),
                                                    "species": new_species,
                                                    "image_path": img_path
                                                })
                                            save_species_samples_table(samples)
                                save_all_catches_raw(all_c)
                                st.session_state[f"show_edit_panel_{row.get('id')}"] = False
                                st.success("Entry updated successfully!")
                                st.rerun()

                        with col_sub_cancel:
                            if st.button("Cancel", key=f"cancel_edit_btn_{row.get('id')}"):
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
