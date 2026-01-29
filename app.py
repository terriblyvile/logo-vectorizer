import streamlit as st
import numpy as np
import cv2
import ezdxf
import subprocess
import os
import tempfile
import zipfile
import io
from svg.path import parse_path, Line, CubicBezier, Move, Close, QuadraticBezier

st.set_page_config(
    page_title="Vectorizer",
    page_icon="📐",
    layout="centered"
)
with st.sidebar:
    st.image(
        "https://play-lh.googleusercontent.com/yAS9WJJnjlCx77RxIvJSssrixhCdUxnBlM3CuPnQpl8QI3Ez19KreBL4xREc1gtmK_Y", 
        use_container_width=True
    )
    st.link_button("Onshape  ↗", "https://cad.onshape.com", type="primary", use_container_width=True)
    st.divider()
    st.info("Brought to you by the Terribly Vile AI Slop Corporation")

def process_image_via_cli(image_bytes, target_width_mm, curve_resolution):

    # PREP IMAGE
    file_bytes = np.asarray(bytearray(image_bytes.read()), dtype=np.uint8)
    image = cv2.imdecode(file_bytes, cv2.IMREAD_GRAYSCALE)
    _, binary = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    
    t_bmp = tempfile.NamedTemporaryFile(delete=False, suffix=".bmp")
    cv2.imwrite(t_bmp.name, binary)
    t_bmp.close()
    
    # RUN POTRACE
    t_svg = tempfile.NamedTemporaryFile(delete=False, suffix=".svg")
    t_svg.close()
    
    cmd = ["potrace", t_bmp.name, "-s", "-o", t_svg.name, "--turdsize", "2", "--alphamax", "1"]
    subprocess.run(cmd, check=True)
    
    # PARSE SVG
    with open(t_svg.name, 'r') as f:
        svg_content = f.read()
    
    import xml.etree.ElementTree as ET
    root = ET.fromstring(svg_content)
    ns = {'svg': 'http://www.w3.org/2000/svg'}
    
    parsed_paths = []
    min_x, max_x, min_y, max_y = float('inf'), float('-inf'), float('inf'), float('-inf')
    
    for node in root.findall(".//svg:path", ns):
        d_str = node.get('d')
        if d_str:
            parsed = parse_path(d_str)
            parsed_paths.append(parsed)
            for segment in parsed:
                for t in [0, 1]:
                    try:
                        p = segment.point(t)
                        min_x = min(min_x, p.real)
                        max_x = max(max_x, p.real)
                        min_y = min(min_y, p.imag)
                        max_y = max(max_y, p.imag)
                    except:
                        pass

    # CALCULATE SCALE
    current_width = max_x - min_x
    if current_width <= 0: scale = 1.0
    else: scale = target_width_mm / current_width
    
    # GENERATE DXF
    doc = ezdxf.new('R2010')
    msp = doc.modelspace()
    
    def to_dxf(pt):
        return (pt.real * scale, pt.imag * scale)

    res = max(2, int(curve_resolution))

    for svg_p in parsed_paths:
        if len(svg_p) == 0: continue

        current_points = []
        start_pt = svg_p[0].start
        current_points.append(to_dxf(start_pt))

        for segment in svg_p:
            if len(current_points) > 0:
                last_dxf_pt = current_points[-1]
                seg_start_dxf = to_dxf(segment.start)
                dist = ((last_dxf_pt[0] - seg_start_dxf[0])**2 + (last_dxf_pt[1] - seg_start_dxf[1])**2)**0.5

                if dist > 0.1:
                    if len(current_points) > 1:
                        msp.add_lwpolyline(current_points)
                    current_points = [to_dxf(segment.start)]

            if isinstance(segment, Line):
                current_points.append(to_dxf(segment.end))
            elif isinstance(segment, (CubicBezier, QuadraticBezier)):
                for t in np.linspace(0, 1, res + 1)[1:]:
                    current_points.append(to_dxf(segment.point(t)))
            elif isinstance(segment, Close):
                if len(current_points) > 1:
                    msp.add_lwpolyline(current_points, close=True)
                current_points = []

        if len(current_points) > 1:
            msp.add_lwpolyline(current_points)

    # SAVE
    final_dxf = tempfile.NamedTemporaryFile(delete=False, suffix=".dxf")
    doc.saveas(final_dxf.name)
    
    try:
        os.unlink(t_bmp.name)
        os.unlink(t_svg.name)
    except:
        pass
    
    return final_dxf.name

# UI
st.title("Vectorizer")
st.write("Convert images to CAD-ready DXF files.")

uploaded_files = st.file_uploader("Upload Images", type=['jpg', 'png', 'jpeg', 'bmp'], accept_multiple_files=True)

col1, col2 = st.columns(2)
with col1:
    target_width = st.number_input(
        "Target Width (mm)", 
        min_value=10, 
        value=100,
        help="The output DXF will be scaled to exactly this width. Useful for ensuring it fits your CAD model immediately."
    )
with col2:
    res_input = st.slider(
        "Curve Detail", 
        min_value=2, max_value=10, value=5,
        help="Higher values = Smoother curves but more points (heavier file)."
    )

if uploaded_files:
    st.write(f"**Selected {len(uploaded_files)} files:**")
    if len(uploaded_files) <= 5:
        cols = st.columns(len(uploaded_files))
        for i, file in enumerate(uploaded_files):
            cols[i].image(file, caption=file.name, use_container_width=True)
    else:
        st.write(", ".join([f.name for f in uploaded_files]))

    if st.button("Generate DXF"):
        progress_bar = st.progress(0)
        processed_files = []

        for i, uploaded_file in enumerate(uploaded_files):
            try:
                uploaded_file.seek(0)
                dxf_path = process_image_via_cli(uploaded_file, target_width, res_input)
                clean_name = os.path.splitext(uploaded_file.name)[0] + ".dxf"
                processed_files.append((clean_name, dxf_path))
            except Exception as e:
                st.error(f"Error processing {uploaded_file.name}: {e}")
            progress_bar.progress((i + 1) / len(uploaded_files))

        if processed_files:
            st.success("Processing Complete!")
            if len(processed_files) == 1:
                fname, fpath = processed_files[0]
                with open(fpath, "rb") as f:
                    st.download_button(f"Download {fname}", f, fname, "application/dxf")
                os.unlink(fpath)
            else:
                zip_buffer = io.BytesIO()
                with zipfile.ZipFile(zip_buffer, "w") as zf:
                    for fname, fpath in processed_files:
                        zf.write(fpath, fname)
                        os.unlink(fpath)
                st.download_button("Download All (.zip)", zip_buffer.getvalue(), "vectors.zip", "application/zip")