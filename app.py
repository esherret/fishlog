# --- TAB 2: CATCH MAP ---
with tab2:
    st.header("Catch Location Map")
    catches = load_json(DB_FILE)
    if catches:
        valid_catches = [c for c in catches if c.get("latitude") and c.get("longitude")]
        if valid_catches:
            avg_lat = sum(c["latitude"] for c in valid_catches) / len(valid_catches)
            avg_lon = sum(c["longitude"] for c in valid_catches) / len(valid_catches)

            # Initialize map with high-resolution Esri World Imagery satellite tiles
            m = folium.Map(
                location=[avg_lat, avg_lon], 
                zoom_start=11,
                tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                attr='Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community'
            )

            for c in valid_catches:
                img_tag = ""
                if os.path.exists(c.get("image_path", "")):
                    img_tag = f'<img src="app/static/{c["image_path"]}" width="150px" style="border-radius:5px; margin-bottom:5px;"/><br>'
                
                popup_html = f"""
                <div style="font-family: sans-serif; width: 180px;">
                    {img_tag}
                    <b>{c.get('species', 'Fish')}</b> ({c.get('length', 0)} in)<br>
                    <b>Date:</b> {c.get('formatted_datetime', c.get('date'))}<br>
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
