FROM jonoh/raspberry-pi-os:2021.5.7

# SSH
RUN SSH_DIR=/home/pi/.ssh && \
    SSH_CONF=/etc/ssh/sshd_config && \
    mkdir -p "${SSH_DIR}" && \
    ln -s /data/ssh/authorized_keys "${SSH_DIR}/authorized_keys" && \
    sed -i -r 's/#? *PasswordAuthentication +(yes|no)/PasswordAuthentication no/' ${SSH_CONF} && \
    sed -i -r 's/#? *ChallengeResponseAuthentication +(yes|no)/ChallengeResponseAuthentication no/' ${SSH_CONF} && \
    sed -i -r 's/#? *UsePAM +(yes|no)/UsePAM no/' ${SSH_CONF} && \
    systemctl enable ssh

# WiFi
COPY wifi-unkill.service /etc/systemd/system/
RUN rm /etc/wpa_supplicant/wpa_supplicant.conf || true && \
    ln -s /data/wifi/wpa_supplicant.conf /etc/wpa_supplicant/wpa_supplicant.conf && \
    systemctl enable wifi-unkill.service
