import os
import json
from datetime import datetime
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
import pandas as pd
import requests
import streamlit as st

# Configure page
st.set_page_config(page_title="Fish Catch Log", page_icon="🎣", layout="wide")

# Directory setup for persistent storage
DATA_DIR = "data"
LURES_DIR = os.path.join(DATA_DIR, "lures")
CATCHES_DIR = os.path.join(DATA_DIR, "catches")
DB_FILE = os.path.join(DATA_DIR, "catches.json")
LURES_FILE = os.path.join(DATA_DIR, "lures.json")

for d in [DATA_DIR, LURES_DIR, CATCHES_DIR]:
    os.makedirs(d, exist_ok=True)


# Helper: Load/Save JSON data
def load_json(filepath):
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []
    return []


def save_json(filepath, data):
    with open(filepath, "w") as f:
        json.dump(data, f, indent=4)


# Helper: Extract EXIF data (Timestamp & GPS)
def extract_exif(image_file):
    try:
        image = Image.open(image_file)
        exif_data = image._getexif()
        if not exif_data:
            return None, None, None

        parsed_exif = {}
        for tag_id, value in exif_data.items():
            tag = TAGS.get(tag_id, tag_id)
            if tag == "GPSInfo":
                gps_data = {}
                for t in value:
                    sub_tag = GPSTAGS.get(t, t)
                    gps_data[sub_tag] = value[t]
                parsed_exif["GPSInfo"] = gps_data
            else:
                parsed_exif[tag] = value

        # Extract timestamp
        timestamp = parsed_exif.get("DateTimeOriginal") or parsed_exif.get(
            "DateTime"
        )
        dt = (
            datetime.strptime(timestamp, "%Y:%m:%d %H:%M:%S")
            if timestamp
            else datetime.now()
        )

        # Extract GPS coordinates
        lat, lon = None, None
        gps_info = parsed_exif.get("GPSInfo")
        if gps_info:

            def convert_to_degrees(value):
                d, m, s = value
                return d + (m / 60.0) + (s / 3600.0)

            lat_val = gps_info.get("GPSLatitude")
            lat_ref = gps_info.get("GPSLatitudeRef")
            lon_val = gps_info.get("GPSLongitude")
            lon_ref = gps_info.get("GPSLongitudeRef")

            if lat_val and lat_ref:
                lat = convert_to_degrees(lat_val)
                if lat_ref != "N":
                    lat = -lat
            if lon_val and lon_ref:
                lon = convert_to_degrees(lon_val)
                if lon_ref != "E":
                    lon = -lon

        return dt, lat, lon
    except Exception:
        return datetime.now(), None, None


# Helper: Weather via NWS API (requires lat/lon)
def get_nws_weather(lat, lon):
    if not lat or not lon:
        return (
            "Location not available in photo EXIF",
            "N/A",
            "N/A",
        )
    try:
        # Get grid points
        points_url = f"https://api.weather.gov/points/{lat},{lon}"
        res = requests.get(points_url, headers={"User-Agent": "(fishapp, test@example.com)"}, timeout=5)
        if res.status_code != 200:
            return "Weather unavailable", "N/A", "N/A"
        
        forecast_url = res.json()["properties"]["forecastHourly"]
        forecast_res = requests.get(forecast_url, headers={"User-Agent": "(fishapp, test@example.com)"}, timeout=5)
        if forecast_res.status_code != 200:
            return "Weather unavailable", "N/A", "N/A"

        period = forecast_res.json()["properties"]["periods"][0]
        temp = f"{period.get('temperature')} {period.get('temperatureUnit')}"
        wind_speed = period.get('windSpeed', 'N/A')
        wind_dir = period.get('windDirection', 'N/A')
        short_forecast = period.get('shortForecast', 'N/A')
        
        weather_desc = f"{short_forecast}, Temp: {temp}"
        return weather_desc, wind_speed, wind_dir
    except Exception:
        return "Error fetching weather", "N/A", "N/A"


# Helper: Moon phase calculation approximation
def get_moon_phase(dt):
    # Simple synodic month calculation based on known new moon reference
    known_new_moon = datetime(2000, 1, 6, 18, 14)
    diff = dt - known_new_moon
    days = diff.total_seconds() / 86400
    lunation = 29.53058867
    phase = (days % lunation) / lunation
    
    if phase < 0.03 or phase > 0.97:
        return "New Moon"
    elif phase < 0.22:
        return "Waxing Crescent"
    elif phase < 0.28:
        return "First Quarter"
    elif phase < 0.47:
        return "Waxing Gibbous"
    elif phase < 0.53:
        return "Full Moon"
    elif phase < 0.72:
        return "Waning Gibbous"
    elif phase < 0.78:
        return "Third Quarter"
    else:
        return "Waning Crescent"


# Navigation Tabs
tab1, tab2, tab3 = st.tabs(["🎣 Log a Catch", "🧩 Manage Lures", "📊 Catch History & Analytics"])

# --- TAB 1: LOG A CATCH ---
with tab1:
    st.header("Log a New Catch")

    # Capture or Upload Photo
    catch_image_file = st.camera_input("Take a photo of your catch")
    if not catch_image_file:
        catch_image_file = st.file_uploader("Or choose catch photo from gallery", type=["jpg", "jpeg", "png"])

    if catch_image_file:
        # Display image preview
        st.image(catch_image_file, caption="Catch Photo", width=350)

        # Extract metadata automatically
        dt, lat, lon = extract_exif(catch_image_file)
        
        st.subheader("Extracted Details")
        col1, col2 = st.columns(2)
        with col1:
            log_date = st.date_input("Date", value=dt.date() if dt else datetime.now().date())
            log_time = st.time_input("Time", value=dt.time() if dt else datetime.now().time())
        with col2:
            manual_lat = st.number_input("Latitude", value=float(lat) if lat else 28.39, format="%.6f")
            manual_lon = st.number_input("Longitude", value=float(lon) if lon else -80.60, format="%.6f")

        # Fetch environmental data based on location/time
        combined_dt = datetime.combine(log_date, log_time)
        weather_desc, wind_speed, wind_dir = get_nws_weather(manual_lat, manual_lon)
        moon_phase = get_moon_phase(combined_dt)

        st.info(f"🌤️ **Weather:** {weather_desc} | 💨 **Wind:** {wind_speed} {wind_dir} | 🌙 **Moon:** {moon_phase}")

        # Fish details
        species = st.text_input("Fish Species (e.g., Snook, Redfish, Trout)")
        length = st.slider("Length (Inches)", min_value=0.0, max_value=40.0, value=15.0, step=0.5)

        # Lure Selection
        lures = load_json(LURES_FILE)
        selected_lure = None
        if lures:
            st.subheader("Select Lure Used")
            lure_cols = st.columns(min(len(lures), 4))
            lure_names = [l["name"] for l in lures]
            chosen_lure_name = st.selectbox("Choose Lure", lure_names)
            for l in lures:
                if l["name"] == chosen_lure_name:
                    selected_lure = l["name"]
                    st.image(l["image_path"], width=150)
        else:
            st.warning("No lures registered yet. Add lures in the 'Manage Lures' tab.")
            selected_lure = st.text_input("Manual Lure Name")

        if st.button("Save Catch Entry", type="primary"):
            # Save image to disk
            img_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            img_path = os.path.join(CATCHES_DIR, img_filename)
            with open(img_path, "wb") as f:
                f.write(catch_image_file.getbuffer())

            # Create record
            record = {
                "date": log_date.strftime("%Y-%m-%d"),
                "time": log_time.strftime("%H:%M:%S"),
                "latitude": manual_lat,
                "longitude": manual_lon,
                "species": species,
                "length": length,
                "lure": selected_lure,
                "weather": weather_desc,
                "wind_speed": wind_speed,
                "wind_direction": wind_dir,
                "moon_phase": moon_phase,
                "image_path": img_path
            }

            catches = load_json(DB_FILE)
            catches.append(record)
            save_json(DB_FILE, catches)
            st.success("Catch successfully logged!")


# --- TAB 2: MANAGE LURES ---
with tab2:
    st.header("Lure Inventory")
    
    with st.form("lure_form", clear_on_submit=True):
        lure_name = st.text_input("Lure Name")
        lure_image = st.file_uploader("Upload Lure Image", type=["jpg", "jpeg", "png"])
        submitted = st.form_submit_button("Add Lure")
        
        if submitted and lure_name and lure_image:
            img_filename = f"lure_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            img_path = os.path.join(LURES_DIR, img_filename)
            with open(img_path, "wb") as f:
            
                f.write(lure_image.getbuffer())
                
            lures = load_json(LURES_FILE)
            lures.append({"name": lure_name, "image_path": img_path})
            save_json(LURES_FILE, lures)
            st.success(f"Added lure: {lure_name}")

    st.subheader("Your Lures")
    lures = load_json(LURES_FILE)
    if lures:
        cols = st.columns(3)
        for idx, lure in enumerate(lures):
            with cols[idx % 3]:
                st.image(lure["image_path"], width=150, caption=lure["name"])


# --- TAB 3: CATCH HISTORY & ANALYTICS ---
with tab3:
    st.header("Catch History & Filters")
    catches = load_json(DB_FILE)
    
    if not catches:
        st.info("No catches logged yet.")
    else:
        df = pd.DataFrame(catches)
        
        # Filters
        st.sidebar.header("Filter Catches")
        species_list = ["All"] + list(df["species"].unique())
        selected_species = st.sidebar.selectbox("Filter by Species", species_list)
        
        moon_list = ["All"] + list(df["moon_phase"].unique())
        selected_moon = st.sidebar.selectbox("Filter by Moon Phase", moon_list)
        
        filtered_df = df.copy()
        if selected_species != "All":
            filtered_df = filtered_df[filtered_df["species"] == selected_species]
        if selected_moon != "All":
            filtered_df = filtered_df[filtered_df["moon_phase"] == selected_moon]
            
        st.subheader("Analytics & Insights")
        if not filtered_df.empty:
            max_length_row = filtered_df.loc[filtered_df["length"].idxmax()]
            st.metric(label="Longest Fish in Filter", value=f"{max_length_row['length']} inches ({max_length_row['species']})")
            st.write(f"**Conditions for Longest Fish:** Wind: {max_length_row['wind_speed']} {max_length_row['wind_direction']} | Moon: {max_length_row['moon_phase']} | Lure: {max_length_row['lure']}")

        st.subheader("Log Entries")
        for _, row in filtered_df.iterrows():
            col1, col2 = st.columns([1, 2])
            with col1:
                if os.path.exists(row["image_path"]):
                    st.image(row["image_path"], width=200)
            with col2:
                st.write(f"**Species:** {row['species']} ({row['length']} inches)")
                st.write(f"**Date/Time:** {row['date']} {row['time']}")
                st.write(f"**Lure:** {row['lure']}")
                st.write(f"**Weather:** {row['weather']} | **Wind:** {row['wind_speed']} {row['wind_direction']}")
                st.write(f"**Moon Phase:** {row['moon_phase']}")
            st.divider()
