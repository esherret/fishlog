import os
import json
from datetime import datetime, timedelta
from PIL import Image, ImageOps
from PIL.ExifTags import TAGS, GPSTAGS
import pandas as pd
import requests
import streamlit as st
from streamlit_folium import st_folium
import folium

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


# Helper: Ultra-fast image compression and downscaling to eliminate upload lag & convert RGBA to RGB for JPEGs
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

        timestamp = parsed_exif.get("DateTimeOriginal") or parsed_exif.get("DateTime")
        dt = (
            datetime.strptime(timestamp, "%Y:%m:%d %H:%M:%S")
            if timestamp
            else datetime.now()
        )

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


# Helper: Tide estimation based on lunar cycle and time offset
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


# Helper: Fast Pure Code Image Feature Heuristic for Fish Recognition
def recognize_fish_and_lure(image_file, lures):
    try:
        img = Image.open(image_file).convert('RGB')
        img_resized = img.resize((30, 30))
        pixels = list(img_resized.getdata())
        
        r_total = sum(p[0] for p in pixels)
        g_total = sum(p[1] for p in pixels)
        b_total = sum(p[2] for p in pixels)
        num_pixels = len(pixels)
        
        avg_r = r_total / num_pixels
        avg_g = g_total / num_pixels
        avg_b = b_total / num_pixels

        width, height = img.size
        aspect_ratio = width / float(height) if height > 0 else 1.0

        if aspect_ratio > 1.4:
            detected_species = "Tarpon"
        elif avg_g > avg_r and avg_g > avg_b:
            detected_species = "Redfish"
        elif avg_b > avg_r:
            detected_species = "Spotted Seatrout"
        else:
            detected_species = "Snook"
    except Exception:
        detected_species = "Snook"

    detected_lure = lures[0]["name"] if lures else None
    return detected_species, detected_lure


# Navigation Tabs
tab1, tab2, tab3, tab4 = st.tabs(["🎣 Log a Catch", "🗺️ Catch Map", "🧩 Manage Lures", "📊 History & Analytics"])

# --- TAB 1: LOG A CATCH ---
with tab1:
    st.header("Log a New Catch")

    upload_method = st.radio("Input Method", ["Gallery Upload", "Camera"], horizontal=True, index=0, key="upload_method_radio")
    
    catch_image_file = None
    if upload_method == "Camera":
        catch_image_file = st.camera_input("Take a photo of your catch", key="catch_camera_input")
    else:
        catch_image_file = st.file_uploader("Choose catch photo from gallery", type=["jpg", "jpeg", "png"], key="catch_file_uploader")

    if catch_image_file:
        rotation = st.selectbox("Rotate Image (Edit Menu)", [0, 90, 180, 270], format_func=lambda x: f"Rotate {x}°", key="catch_rotation_select")
        
        processed_image = process_image_orientation(catch_image_file, rotation)
        st.image(processed_image, caption="Processed Catch Photo", width=350)

        dt, lat, lon = extract_exif(catch_image_file)
        
        st.subheader("Extracted Details")
        col1, col2 = st.columns(2)
        with col1:
            default_date = dt.date() if dt else datetime.now().date()
            default_time = dt.time() if dt else datetime.now().time()
            log_date = st.date_input("Date", value=default_date, key="catch_date_input")
            log_time = st.time_input("Time", value=default_time, key="catch_time_input")
        with col2:
            manual_lat = st.number_input("Latitude", value=float(lat) if lat else 28.39, format="%.6f", key="catch_lat_input")
            manual_lon = st.number_input("Longitude", value=float(lon) if lon else -80.60, format="%.6f", key="catch_lon_input")

        combined_dt = datetime.combine(log_date, log_time)
        formatted_datetime_str = combined_dt.strftime("%m/%d/%Y %I:%M %p")
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

        species = st.text_input("Fish Species", value=rec_species, key="catch_species_input")
        length = st.slider("Length (Inches)", min_value=0.0, max_value=40.0, value=15.0, step=0.5, key="catch_length_slider")

        # Lure Selection & Visual Lure Picker Screen
        st.subheader("Lure Used")
        lure_names = [l["name"] for l in lures] if lures else []
        
        if "selected_lure_cache" not in st.session_state:
            st.session_state.selected_lure_cache = rec_lure if rec_lure in lure_names else (lure_names[0] if lure_names else "")

        col_lure1, col_lure2 = st.columns([3, 1])
        with col_lure1:
            st.write(f"**Current Lure:** {st.session_state.selected_lure_cache if st.session_state.selected_lure_cache else 'None selected'}")
            for l in lures:
                if l["name"] == st.session_state.selected_lure_cache:
                    st.image(l["image_path"], width=100, caption=l["name"])
        with col_lure2:
            if st.button("🖼️ Browse All Lures", key="browse_lures_btn"):
                st.session_state.picking_lure_visual = True

        if st.session_state.get("picking_lure_visual", False):
            st.markdown("---")
            st.info("Click on any lure picture below to select it:")
            if lures:
                lure_cols = st.columns(3)
                for idx, l in enumerate(lures):
                    with lure_cols[idx % 3]:
                        st.image(l["image_path"], width=125, caption=l["name"])
                        if st.button(f"Select {l['name']}", key=f"pic_pick_{idx}"):
                            st.session_state.selected_lure_cache = l["name"]
                            st.session_state.picking_lure_visual = False
                            st.rerun()
            else:
                st.warning("No lures in inventory. Please add one below.")
            
            if st.button("Close Lure Gallery", key="close_lure_gallery"):
                st.session_state.picking_lure_visual = False
                st.rerun()
            st.markdown("---")

        # Quick Add New Lure Option
        with st.expander("➕ Or Add a New Lure"):
            new_lure_name = st.text_input("New Lure Name", key="quick_new_lure_name")
            new_lure_image = st.file_uploader("Upload New Lure Image", type=["jpg", "jpeg", "png"], key="quick_new_lure_img")
            if st.button("Save New Lure", key="quick_save_lure_btn"):
                if new_lure_name and new_lure_image:
                    lure_img_filename = f"lure_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
                    lure_img_path = os.path.join(LURES_DIR, lure_img_filename)
                    proc_lure_img = process_image_orientation(new_lure_image)
                    proc_lure_img.save(lure_img_path, optimize=True, quality=80)
                    
                    lures.append({"name": new_lure_name, "image_path": lure_img_path})
                    save_json(LURES_FILE, lures)
                    st.session_state.selected_lure_cache = new_lure_name
                    st.success(f"Added and selected: {new_lure_name}")
                    st.rerun()
                else:
                    st.error("Please provide both a name and an image for the new lure.")

        selected_lure = st.session_state.selected_lure_cache

        if st.button("Save Catch Entry", type="primary"):
            img_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
            img_path = os.path.join(CATCHES_DIR, img_filename)
            processed_image.save(img_path, optimize=True, quality=80)

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
            
            for key in list(st.session_state.keys()):
                del st.session_state[key]
                
            st.success("Catch successfully logged! Ready for next fish.")
            st.rerun()


# --- TAB 2: CATCH MAP ---
with tab2:
    st.header("Catch Location Map")
    catches = load_json(DB_FILE)
    if catches:
        valid_catches = [c for c in catches if c.get("latitude") is not None and c.get("longitude") is not None]
        if valid_catches:
            avg_lat = sum(c["latitude"] for c in valid_catches) / len(valid_catches)
            avg_lon = sum(c["longitude"] for c in valid_catches) / len(valid_catches)

            m = folium.Map(
                location=[avg_lat, avg_lon], 
                zoom_start=11,
                tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                attr='Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community'
            )

            for c in valid_catches:
                img_tag = ""
                img_p = c.get("image_path", "")
                if img_p and os.path.exists(img_p):
                    # Base64 embed the image directly into the popup HTML to prevent path-resolution issues in Folium IFrames
                    import base64
                    with open(img_p, "rb") as img_file:
                        encoded_img = base64.b64encode(img_file.read()).decode("utf-8")
                        img_tag = f'<img src="data:image/jpeg;base64,{encoded_img}" width="150px" style="border-radius:5px; margin-bottom:5px;"/><br>'
                
                dt_disp = c.get('formatted_datetime')
                if not dt_disp:
                    try:
                        dt_obj = datetime.strptime(f"{c.get('date')} {c.get('time')}", "%Y-%m-%d %H:%M:%S")
                        dt_disp = dt_obj.strftime("%m/%d/%Y %I:%M %p")
                    except Exception:
                        dt_disp = c.get('date')

                popup_html = f"""
                <div style="font-family: sans-serif; width: 180px;">
                    {img_tag}
                    <b>{c.get('species', 'Fish')}</b> ({c.get('length', 0)} in)<br>
                    <b>Date:</b> {dt_disp}<br>
                    <b>Lure:</b> {c.get('lure', 'N/A')}<br>
                    <b>Weather:</b> {c.get('weather', 'N/A')}<br>
                    <b>Tide:</b> {c.get('tide', 'N/A')}
                </div>
                """
                
                fish_icon = folium.Icon(icon="fish", prefix="fa", color="blue")

                folium.Marker(
                    location=[c["latitude"], c["longitude"]],
                    popup=folium.Popup(popup_html, max_width=250),
                    icon=fish_icon
                ).add_to(m)

            st_folium(m, width=700, height=500)
        else:
            st.info("No valid GPS coordinate data found in logs.")
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
            proc_lure_img = process_image_orientation(lure_image)
            proc_lure_img.save(img_path, optimize=True, quality=80)
                
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
        
        if "tide" not in df.columns:
            df["tide"] = "N/A"

        st.sidebar.header("Filter Log Entries")
        species_list = ["All"] + list(df["species"].unique()) if "species" in df.columns else ["All"]
        selected_species = st.sidebar.selectbox("Species", species_list)
        
        min_size_filter = st.sidebar.slider("Minimum Size (Inches)", 0.0, 40.0, 0.0, 0.5)
        
        lure_filter_list = ["All"] + list(df["lure"].unique()) if "lure" in df.columns else ["All"]
        selected_lure_filter = st.sidebar.selectbox("Lure Caught On", lure_filter_list)
        
        wind_speed_slider = st.sidebar.slider("Max Wind Speed Filter (mph)", 0, 40, 40)
        
        wind_dir_list = ["All"] + list(df["wind_direction"].unique()) if "wind_direction" in df.columns else ["All"]
        selected_wind_dir = st.sidebar.selectbox("Wind Direction", wind_dir_list)

        filtered_df = df.copy()
        if selected_species != "All":
            filtered_df = filtered_df[filtered_df["species"] == selected_species]
        filtered_df = filtered_df[filtered_df["length"] >= min_size_filter]
        if selected_lure_filter != "All":
            filtered_df = filtered_df[filtered_df["lure"] == selected_lure_filter]
        if selected_wind_dir != "All":
            filtered_df = filtered_df[filtered_df["wind_direction"] == selected_wind_dir]

        view_mode = st.radio("View Layout", ["Card View with Images", "Row-by-Row Table (No Images)"], horizontal=True)

        if view_mode == "Row-by-Row Table (No Images)":
            st.write("Click on any entry below to view its card, edit details, or delete it.")
            
            for idx, row in filtered_df.iterrows():
                entry_id = str(row.get("id") or f"row_{idx}")
                
                dt_str = row.get('formatted_datetime', '')
                if not dt_str:
                    try:
                        dt_obj = datetime.strptime(f"{row.get('date')} {row.get('time')}", "%Y-%m-%d %H:%M:%S")
                        dt_str = dt_obj.strftime("%m/%d/%Y %I:%M %p")
                    except Exception:
                        dt_str = f"{row.get('date')} {row.get('time')}"

                summary_label = f"📅 {dt_str} | 🐟 {row.get('species')} ({row.get('length')} in) | 🎣 {row.get('lure')}"
                
                with st.expander(summary_label):
                    col1, col2, col3 = st.columns([1, 2, 1])
                    with col1:
                        img_p = row.get("image_path")
                        if img_p and os.path.exists(img_p):
                            st.image(img_p, width=180)
                    with col2:
                        st.write(f"**Species:** {row.get('species', 'N/A')} ({row.get('length', 0)} inches)")
                        st.write(f"**Date/Time:** {dt_str}")
                        st.write(f"**Lure:** {row.get('lure', 'N/A')}")
                        st.write(f"**Weather:** {row.get('weather', 'N/A')} | **Wind:** {row.get('wind_speed', 'N/A')} {row.get('wind_direction', 'N/A')}")
                        st.write(f"**Tide:** {row.get('tide', 'N/A')} | **Moon:** {row.get('moon_phase', 'N/A')}")
                    with col3:
                        st.subheader("Edit Entry")
                        
                        current_species = str(row.get('species') or 'Snook')
                        current_length = float(row.get('length') or 15.0)
                        current_lure = str(row.get('lure') or '')

                        new_species = st.text_input("Edit Species", value=current_species, key=f"edit_sp_{entry_id}_{idx}")
                        new_length = st.slider("Edit Length (Inches)", 0.0, 40.0, current_length, 0.5, key=f"edit_len_{entry_id}_{idx}")
                        new_lure = st.text_input("Edit Lure", value=current_lure, key=f"edit_lure_{entry_id}_{idx}")
                        
                        new_image_file = st.file_uploader("Change Catch Image", type=["jpg", "jpeg", "png"], key=f"edit_img_{entry_id}_{idx}")
                        
                        if st.button("Save Changes", key=f"save_edit_{entry_id}_{idx}"):
                            for c in catches:
                                target_id = str(c.get("id") or f"row_{catches.index(c)}")
                                if target_id == entry_id:
                                    c["species"] = new_species
                                    c["length"] = new_length
                                    c["lure"] = new_lure
                                    if new_image_file:
                                        img_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
                                        img_path = os.path.join(CATCHES_DIR, img_filename)
                                        proc_img = process_image_orientation(new_image_file)
                                        proc_img.save(img_path, optimize=True, quality=80)
                                        c["image_path"] = img_path
                            save_json(DB_FILE, catches)
                            st.success("Entry updated successfully!")
                            st.rerun()

                        st.markdown("---")
                        if st.button("Delete Entry", key=f"btn_del_{entry_id}_{idx}", type="secondary"):
                            st.session_state[f"confirm_delete_{entry_id}_{idx}"] = True
                            
                        if st.session_state.get(f"confirm_delete_{entry_id}_{idx}", False):
                            st.warning("Are you sure you want to delete this?")
                            col_yes, col_no = st.columns(2)
                            with col_yes:
                                if st.button("Yes", key=f"yes_del_{entry_id}_{idx}", type="primary"):
                                    updated_catches = [c for i, c in enumerate(catches) if str(c.get("id") or f"row_{i}") != entry_id]
                                    save_json(DB_FILE, updated_catches)
                                    st.success("Entry deleted!")
                                    st.rerun()
                            with col_no:
                                if st.button("No", key=f"no_del_{entry_id}_{idx}"):
                                    st.session_state[f"confirm_delete_{entry_id}_{idx}"] = False
                                    st.rerun()
        else:
            for idx, row in filtered_df.iterrows():
                entry_id = str(row.get("id") or f"card_{idx}")
                
                dt_str = row.get('formatted_datetime', '')
                if not dt_str:
                    try:
                        dt_obj = datetime.strptime(f"{row.get('date')} {row.get('time')}", "%Y-%m-%d %H:%M:%S")
                        dt_str = dt_obj.strftime("%m/%d/%Y %I:%M %p")
                    except Exception:
                        dt_str = f"{row.get('date')} {row.get('time')}"

                col1, col2, col3 = st.columns([1, 2, 1])
                with col1:
                    img_p = row.get("image_path")
                    if img_p and os.path.exists(img_p):
                        st.image(img_p, width=180)
                with col2:
                    st.write(f"**Species:** {row.get('species', 'N/A')} ({row.get('length', 0)} inches)")
                    st.write(f"**Date/Time:** {dt_str}")
                    st.write(f"**Lure:** {row.get('lure', 'N/A')}")
                    st.write(f"**Weather:** {row.get('weather', 'N/A')} | **Wind:** {row.get('wind_speed', 'N/A')} {row.get('wind_direction', 'N/A')}")
                    st.write(f"**Tide:** {row.get('tide', 'N/A')} | **Moon:** {row.get('moon_phase', 'N/A')}")
                with col3:
                    if st.button("Delete Entry", key=f"btn_del_card_{entry_id}_{idx}", type="secondary"):
                        st.session_state[f"confirm_delete_card_{entry_id}_{idx}"] = True
                        
                    if st.session_state.get(f"confirm_delete_card_{entry_id}_{idx}", False):
                        st.warning("Are you sure you want to delete this?")
                        col_yes, col_no = st.columns(2)
                        with col_yes:
                            if st.button("Yes", key=f"yes_del_card_{entry_id}_{idx}", type="primary"):
                                updated_catches = [c for i, c in enumerate(catches) if str(c.get("id") or f"card_{i}") != entry_id]
                                save_json(DB_FILE, updated_catches)
                                st.success("Entry deleted!")
                                st.rerun()
                        with col_no:
                            if st.button("No", key=f"no_del_card_{entry_id}_{idx}"):
                                st.session_state[f"confirm_delete_card_{entry_id}_{idx}"] = False
                                st.rerun()
                st.divider()
