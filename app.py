import os
import json
from datetime import datetime, timedelta
from PIL import Image, ImageOps
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


# Helper: Correct image orientation from EXIF and apply rotation if needed
def process_image_orientation(image, rotation_angle=0):
    try:
        image = ImageOps.exif_transpose(image)
    except Exception:
        pass
    
    if rotation_angle != 0:
        image = image.rotate(rotation_angle, expand=True)
        
    return image


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
        timestamp = parsed_exif.get("DateTimeOriginal") or parsed_exif.get("DateTime")
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


# Helper: Weather & Wind via NWS API
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
        wind_speed_str = period.get('windSpeed', '0 mph')
        wind_dir = period.get('windDirection', 'N/A')
        short_forecast = period.get('shortForecast', 'N/A')
        
        weather_desc = f"{short_forecast}, Temp: {temp}"
        return weather_desc, wind_speed_str, wind_dir
    except Exception:
        return "Error fetching weather", "N/A", "N/A"


# Helper: Tide estimation based on NOAA API or approximation framework
def get_tide_info(lat, lon, dt):
    if not lat or not lon:
        return "Tide info unavailable (No GPS)"
    try:
        hour_offset = (dt.hour + dt.minute / 60.0) % 12.42
        if hour_offset < 3.1:
            return "Incoming (Rising)"
        elif hour_offset < 6.2:
            return "High Tide"
        elif hour_offset < 9.3:
            return "Outgoing (Falling)"
        else:
            return "Low Tide"
    except Exception:
        return "Tide calculation error"


# Helper: Moon phase calculation
def get_moon_phase(dt):
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


# Helper: Automatic Species and Lure Recognition Mock/Heuristic
def recognize_fish_and_lure(image_file, lures):
    detected_species = "Snook"
    detected_lure = lures[0]["name"] if lures else None
    return detected_species, detected_lure


# Navigation Tabs
tab1, tab2, tab3, tab4 = st.tabs(["🎣 Log a Catch", "🗺️ Catch Map", "🧩 Manage Lures", "📊 History & Analytics"])

# --- TAB 1: LOG A CATCH ---
with tab1:
    st.header("Log a New Catch")

    upload_method = st.radio("Input Method", ["Camera", "Gallery Upload"], horizontal=True, key="upload_method_radio")
    
    catch_image_file = None
    if upload_method == "Camera":
        catch_image_file = st.camera_input("Take a photo of your catch")
    else:
        catch_image_file = st.file_uploader("Choose catch photo from gallery", type=["jpg", "jpeg", "png"])

    if catch_image_file:
        rotation = st.selectbox("Rotate Image (Edit Menu)", [0, 90, 180, 270], format_func=lambda x: f"Rotate {x}°")
        
        raw_image = Image.open(catch_image_file)
        processed_image = process_image_orientation(raw_image, rotation)
        
        st.image(processed_image, caption="Processed Catch Photo", width=350)

        dt, lat, lon = extract_exif(catch_image_file)
        
        st.subheader("Extracted Details")
        col1, col2 = st.columns(2)
        with col1:
            default_date = dt.date() if dt else datetime.now().date()
            default_time = dt.time() if dt else datetime.now().time()
            log_date = st.date_input("Date", value=default_date)
            log_time = st.time_input("Time", value=default_time)
        with col2:
            manual_lat = st.number_input("Latitude", value=float(lat) if lat else 28.39, format="%.6f")
            manual_lon = st.number_input("Longitude", value=float(lon) if lon else -80.60, format="%.6f")

        combined_dt = datetime.combine(log_date, log_time)
        formatted_datetime_str = combined_dt.strftime("%d/%m/%Y %I:%M %p")
        st.write(f"**Logged Timestamp:** {formatted_datetime_str}")

        catches = load_json(DB_FILE)
        is_duplicate = False
        for c in catches:
            try:
                existing_dt = datetime.strptime(f"{c['date']} {c['time']}", "%Y-%m-%d %H:%M:%S")
                if abs((combined_dt - existing_dt).total_seconds()) <= 180:
                    is_duplicate = True
                    break
            except Exception:
                continue
        
        if is_duplicate:
            st.warning("⚠️ **Warning:** Another catch entry exists within a 3-minute window of this timestamp. You might be adding duplicate logs for the same fish!")

        weather_desc, wind_speed_str, wind_dir = get_nws_weather(manual_lat, manual_lon)
        moon_phase = get_moon_phase(combined_dt)
        tide_info = get_tide_info(manual_lat, manual_lon, combined_dt)

        st.info(f"🌤️ **Weather:** {weather_desc} | 💨 **Wind:** {wind_speed_str} {wind_dir} | 🌊 **Tide:** {tide_info} | 🌙 **Moon:** {moon_phase}")

        lures = load_json(LURES_FILE)
        rec_species, rec_lure = recognize_fish_and_lure(catch_image_file, lures)

        species = st.text_input("Fish Species", value=rec_species)
        length = st.slider("Length (Inches)", min_value=0.0, max_value=40.0, value=15.0, step=0.5)

        selected_lure = None
        if lures:
            lure_names = [l["name"] for l in lures]
            default_idx = lure_names.index(rec_lure) if rec_lure in lure_names else 0
            chosen_lure_name = st.selectbox("Select Lure from Inventory", lure_names, index=default_idx)
            
            for l in lures:
                if l["name"] == chosen_lure_name:
                    selected_lure = l["name"]
                    st.image(l["image_path"], width=120, caption=l["name"])
        else:
            st.warning("No lures in inventory. Please add a new lure below.")
            new_lure_prompt = st.text_input("New Lure Name to Add")
            if new_lure_prompt:
                selected_lure = new_lure_prompt

        if st.button("Save Catch Entry", type="primary"):
            img_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
            img_path = os.path.join(CATCHES_DIR, img_filename)
            processed_image.save(img_path)

            record = {
                "id": img_filename,
                "date": log_date.strftime("%Y-%m-%d"),
                "time": log_time.strftime("%H:%M:%S"),
                "formatted_datetime": formatted_datetime_str,
                "latitude": manual_lat,
                "longitude": manual_lon,
                "species": species,
                "length": length,
                "lure": selected_lure,
                "weather": weather_desc,
                "wind_speed": wind_speed_str,
                "wind_direction": wind_dir,
                "tide": tide_info,
                "moon_phase": moon_phase,
                "image_path": img_path
            }

            catches.append(record)
            save_json(DB_FILE, catches)
            st.success("Catch successfully logged!")


# --- TAB 2: CATCH MAP ---
with tab2:
    st.header("Catch Location Map")
    catches = load_json(DB_FILE)
    if catches:
        map_df = pd.DataFrame(catches)
        if "latitude" in map_df.columns and "longitude" in map_df.columns:
            st.map(map_df, latitude="latitude", longitude="longitude", size=30, color=None)
        else:
            st.info("No GPS coordinate data found in logs.")
    else:
        st.info("No catches recorded yet to display on map.")


# --- TAB 3: MANAGE LURES ---
with tab3:
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


# --- TAB 4: HISTORY & ANALYTICS ---
with tab4:
    st.header("Catch History, Filtering & Management")
    catches = load_json(DB_FILE)
    
    if not catches:
        st.info("No catches logged yet.")
    else:
        df = pd.DataFrame(catches)
        
        # Ensure 'tide' column exists safely if older records are present
        if "tide" not in df.columns:
            df["tide"] = "N/A"

        # Sidebar Filters
        st.sidebar.header("Filter Log Entries")
        species_list = ["All"] + list(df["species"].unique()) if "species" in df.columns else ["All"]
        selected_species = st.sidebar.selectbox("Species", species_list)
        
        min_size_filter = st.sidebar.slider("Minimum Size (Inches)", 0.0, 40.0, 0.0, 0.5)
        
        lure_filter_list = ["All"] + list(df["lure"].unique()) if "lure" in df.columns else ["All"]
        selected_lure_filter = st.sidebar.selectbox("Lure Caught On", lure_filter_list)
        
        wind_speed_slider = st.sidebar.slider("Max Wind Speed Filter (mph)", 0, 40, 40)
        
        wind_dir_list = ["All"] + list(df["wind_direction"].unique()) if "wind_direction" in df.columns else ["All"]
        selected_wind_dir = st.sidebar.selectbox("Wind Direction", wind_dir_list)

        # Apply filtering logic safely
        filtered_df = df.copy()
        if selected_species != "All":
            filtered_df = filtered_df[filtered_df["species"] == selected_species]
        filtered_df = filtered_df[filtered_df["length"] >= min_size_filter]
        if selected_lure_filter != "All":
            filtered_df = filtered_df[filtered_df["lure"] == selected_lure_filter]
        if selected_wind_dir != "All":
            filtered_df = filtered_df[filtered_df["wind_direction"] == selected_wind_dir]

        # View format option
        view_mode = st.radio("View Layout", ["Card View with Images", "Row-by-Row Table (No Images)"], horizontal=True)

        if view_mode == "Row-by-Row Table (No Images)":
            display_cols = ["date", "time", "species", "length", "lure", "weather", "wind_speed", "wind_direction", "tide", "moon_phase"]
            available_display_cols = [col for col in display_cols if col in filtered_df.columns]
            st.dataframe(filtered_df[available_display_cols], use_container_width=True)
        else:
            for _, row in filtered_df.iterrows():
                col1, col2, col3 = st.columns([1, 2, 1])
                with col1:
                    img_p = row.get("image_path")
                    if img_p and os.path.exists(img_p):
                        st.image(img_p, width=180)
                with col2:
                    st.write(f"**Species:** {row.get('species', 'N/A')} ({row.get('length', 0)} inches)")
                    st.write(f"**Date/Time:** {row.get('formatted_datetime', str(row.get('date', '')) + ' ' + str(row.get('time', '')))}")
                    st.write(f"**Lure:** {row.get('lure', 'N/A')}")
                    st.write(f"**Weather:** {row.get('weather', 'N/A')} | **Wind:** {row.get('wind_speed', 'N/A')} {row.get('wind_direction', 'N/A')}")
                    st.write(f"**Tide:** {row.get('tide', 'N/A')} | **Moon:** {row.get('moon_phase', 'N/A')}")
                with col3:
                    entry_id = row.get("id")
                    if entry_id and st.button("Delete", key=f"del_{entry_id}"):
                        updated_catches = [c for c in catches if c.get("id") != entry_id]
                        save_json(DB_FILE, updated_catches)
                        st.success("Entry deleted!")
                        st.rerun()
                st.divider()
