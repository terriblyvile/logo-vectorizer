import streamlit as st
import numpy as np
import cv2
import ezdxf
import subprocess
import os
import tempfile
import zipfile
import io
import base64
import trimesh
from svg.path import parse_path, Line, CubicBezier, Move, Close, QuadraticBezier
import streamlit.components.v1 as components

# --- 1. SET PAGE CONFIG ---
st.set_page_config(
    page_title="The Vectorizer",
    page_icon="📐",
    layout="centered"
)

# --- SIDEBAR ---
with st.sidebar:
    st.image(
        "https://play-lh.googleusercontent.com/yAS9WJJnjlCx77RxIvJSssrixhCdUxnBlM3CuPnQpl8QI3Ez19KreBL4xREc1gtmK_Y", 
        use_container_width=True
    )
    st.link_button("Open Onshape ↗", "https://cad.onshape.com", type="primary", use_container_width=True)
    st.divider()
    st.info("Terribly vile AI slop.")

# --- 3D PREVIEW HELPER ---
def render_3d_preview(svg_path, height=5):
    """
    Loads the SVG, extrudes it into a 3D mesh, and returns a GLB base64 string.
    """
    try:
        # Load the SVG path
        # force 'svg' type to be safe
        s = trimesh.load_path(svg_path, file_type='svg')
        
        # Extrude the 2D path to 3D
        mesh_data = s.extrude(height)
        
        # Handle multiple separate parts (like letters)
        if isinstance(mesh_data, list) or isinstance(mesh_data, tuple):
            mesh = trimesh.util.concatenate(mesh_data)
        else:
            mesh = mesh_data
        
        # --- FIX: FLIP VERTICALLY ---
        # Create a 4x4 rotation matrix: 180 degrees around the X-axis
        # This flips Y (up/down) and Z (depth), fixing the "upside down" issue
        matrix = trimesh.transformations.rotation_matrix(np.pi, [1, 0, 0])
        mesh.apply_transform(matrix)
        
        # Color it "Onshape Blue"
        mesh.visual.face_colors = [23, 87, 136, 255]
        
        # Export to GLB
        glb_data = mesh.export(file_type='glb')
        
        # Encode
        b64 = base64.b64encode(glb_data).decode('utf-8')
        return b64
    except Exception as e:
        print(f"3D Generation Error: {e}")
        return None

# --- CORE LOGIC ---
def process_image_via_cli(image_bytes, target_width_mm, curve_resolution):
    # --- PREP IMAGE ---
    file_bytes = np.asarray(bytearray(image_bytes.read()), dtype=np.uint8)
    image = cv2.imdecode(file_bytes, cv2.IMREAD_GRAYSCALE)
    _, binary = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    
    t_bmp = tempfile.NamedTemporaryFile(delete=False, suffix=".bmp")
    cv2.imwrite(t_bmp.name, binary)
    t_bmp.close()
    
    # --- RUN POTRACE ---
    t_svg = tempfile.NamedTemporaryFile(delete=False, suffix=".svg")
    t_svg.close()
    
    cmd = ["potrace", t_bmp.name, "-s", "-o", t_svg.name, "--turdsize", "2", "--alphamax", "1"]
    subprocess.run(cmd, check=True)
    
    # --- PARSE SVG FOR DXF ---
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

    # Calculate Scale
    current_width = max_x - min_x
    if current_width <= 0: scale = 1.0
    else: scale = target_width_mm / current_width
    
    # --- GENERATE DXF ---
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

    # --- SAVE ---
    final_dxf = tempfile.NamedTemporaryFile(delete=False, suffix=".dxf")
    doc.saveas(final_dxf.name)
    
    # Return both the DXF path and the SVG path (for 3D generation)
    return final_dxf.name, t_svg.name, t_bmp.name

# --- UI ---
st.title("Auto-Tracer")
st.write("Convert raster images to CAD-ready DXF files.")

uploaded_files = st.file_uploader("Upload Images", type=['jpg', 'png', 'jpeg', 'bmp'], accept_multiple_files=True)

col1, col2 = st.columns(2)
with col1:
    target_width = st.number_input(
        "Target Width (mm)", 
        min_value=10, 
        value=100,
        help="The output DXF will be scaled to exactly this width."
    )
with col2:
    res_input = st.slider(
        "Curve Detail", 
        min_value=2, max_value=20, value=6,
        help="Higher values = Smoother curves but more points."
    )

if uploaded_files:
    # Preview logic
    st.write(f"**Selected {len(uploaded_files)} files:**")
    if len(uploaded_files) <= 5:
        cols = st.columns(len(uploaded_files))
        for i, file in enumerate(uploaded_files):
            cols[i].image(file, caption=file.name, use_container_width=True)
    else:
        st.write(", ".join([f.name for f in uploaded_files]))

    if st.button("Generate All DXFs"):
        progress_bar = st.progress(0)
        processed_files = []
        last_svg_path = None
        
        for i, uploaded_file in enumerate(uploaded_files):
            try:
                uploaded_file.seek(0)
                # Unpack the tuple return
                dxf_path, svg_path, bmp_path = process_image_via_cli(uploaded_file, target_width, res_input)
                
                clean_name = os.path.splitext(uploaded_file.name)[0] + ".dxf"
                processed_files.append((clean_name, dxf_path))
                
                # Keep track of the last SVG for 3D preview (if single file)
                last_svg_path = svg_path
                
                # Cleanup BMP immediately, keep SVG for a moment if needed
                os.unlink(bmp_path)
                
            except Exception as e:
                st.error(f"Error processing {uploaded_file.name}: {e}")
            progress_bar.progress((i + 1) / len(uploaded_files))

        if processed_files:
            st.success("Processing Complete!")
            
            # --- 3D PREVIEW (Only if 1 file uploaded) ---
            if len(uploaded_files) == 1 and last_svg_path:
                with st.spinner("Generating 3D Preview..."):
                    b64_model = render_3d_preview(last_svg_path, height=5)
                    if b64_model:
                        st.markdown("### 3D Extrusion Preview (5mm)")
                        # Embed <model-viewer>
                        viewer_html = f"""
                        <script type="module" src="https://ajax.googleapis.com/ajax/libs/model-viewer/3.4.0/model-viewer.min.js"></script>
                        <model-viewer 
                            src="data:model/gltf-binary;base64,{b64_model}" 
                            alt="3D Preview"
                            auto-rotate
                            camera-controls
                            style="width: 100%; height: 400px; background-color: #f0f2f6; border-radius: 10px;"
                            shadow-intensity="1">
                        </model-viewer>
                        """
                        components.html(viewer_html, height=420)
                
                # Cleanup SVG after preview
                os.unlink(last_svg_path)
            else:
                # Cleanup SVGs if batch mode
                if last_svg_path and os.path.exists(last_svg_path):
                    os.unlink(last_svg_path)

            # --- DOWNLOAD BUTTONS ---
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