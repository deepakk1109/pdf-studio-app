FROM linuxserver/webtop:ubuntu-openbox

# install python and qt dependencies
RUN apt update && apt install -y \
    python3 \
    python3-pip \
    python3-venv \
    libxcb-cursor0 \
    libxcb-xinerama0 \
    libxcb-icccm4 \
    libxcb-image0 \
    libxcb-keysyms1 \
    libxcb-randr0 \
    libxcb-render-util0 \
    libxcb-shape0 \
    libxkbcommon-x11-0 \
    libgl1 \
    libglx-mesa0

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages --ignore-installed -r requirements.txt

COPY pdf_studio.py .

# auto-launch the app inside the webtop desktop
RUN echo "python3 /app/pdf_studio.py &" >> /config/.bashrc

EXPOSE 3000

# # FROM linuxserver/webtop:ubuntu-openbox

# # # install python and qt dependencies
# # RUN apt update && apt install -y \
# #     python3 \
# #     python3-pip \
# #     python3-venv \
# #     libxcb-cursor0 \
# #     libxcb-xinerama0 \
# #     libxcb-icccm4 \
# #     libxcb-image0 \
# #     libxcb-keysyms1 \
# #     libxcb-randr0 \
# #     libxcb-render-util0 \
# #     libxcb-shape0 \
# #     libxkbcommon-x11-0 \
# #     libgl1 \
# #     libglx-mesa0

# # WORKDIR /app

# # COPY requirements.txt .
# # RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# COPY pdf_studio.py .

# # auto-launch the app inside the webtop desktop
# RUN echo "python3 /app/pdf_studio.py &" >> /config/.bashrc

# EXPOSE 3000
