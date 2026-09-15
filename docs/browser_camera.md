# Cameras on the hosted app

The hosted app receives camera frames through WebRTC in your browser. Camera
indices on the server cannot access cameras attached to your computer.

Deploy `breaking-code` with its updated `requirements.txt`. On Linux with no
camera devices, the default `CAMERA_INPUT_MODE=auto` selects browser capture.
To select it explicitly, add this top-level entry in Streamlit Cloud Secrets:

```toml
CAMERA_INPUT_MODE = "browser"
```

Keep your existing secrets. Do not commit credentials to Git.

1. Open the deployed HTTPS site on the computer running the DroidCam client.
2. Open Dash Cam and allow browser camera permission if prompted. Cabin preview
   starts automatically using the browser's default camera.
3. The default browser camera supplies the cabin feed in the existing Live Dash
   Cam preview. There is no separate camera setup panel. To change the default
   device, use your browser's camera permissions/settings for this site.
4. Press Start Recording to save video, then Stop Recording to finalize it.
   Close Preview stops the camera stream; Open / Retry Preview creates a fresh
   connection, including when the previous attempt stalled.

Keep the page open while recording. A disconnected stream closes its video
writer; return to the page and press Stop Recording to finish processing.
Browser sessions have separate capture channels. Driver identity and fatigue
monitoring currently require local capture mode.

## Connection stays on connecting

The default STUN configuration cannot connect through every hosting network,
firewall or mobile network. Such connections require a working TURN service.
Obtain relay URLs and credentials from your TURN provider, then add a top-level
secret with the provider's values (the following values are placeholders):

```toml
ROADWATCH_WEBRTC_ICE_SERVERS = '[{"urls":["stun:stun.l.google.com:19302"]},{"urls":["turns:YOUR_TURN_HOST:443?transport=tcp"],"username":"YOUR_TURN_USERNAME","credential":"YOUR_TURN_PASSWORD"}]'
```

Reload the app after changing secrets. Do not enter the phone's private LAN
address as a cloud camera source: select DroidCam through the browser instead.

## Verification

`python -m unittest discover -s tests` exercises capture isolation and the
existing application regressions. `python scripts/verify_browser_transport.py`
connects two local WebRTC peers, sends synthetic images and decodes the resulting
WebM recording. This verifies transport and recording, but does not replace a
real camera test on the deployed site or verify a TURN provider.
