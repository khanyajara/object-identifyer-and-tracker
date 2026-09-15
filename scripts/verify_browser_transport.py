"""Real local WebRTC transport and recorder smoke test using synthetic frames."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def verify():
    import av
    import cv2
    import numpy as np
    from aiortc import RTCPeerConnection, RTCConfiguration, VideoStreamTrack
    from core.dual_camera import CameraConfig
    from core.browser_camera import BrowserCameraChannel

    class SyntheticTrack(VideoStreamTrack):
        async def recv(self):
            pts, base = await self.next_timestamp()
            image = np.full((120, 160, 3), (pts//3000) % 200 + 20, dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(image, format="bgr24")
            frame.pts, frame.time_base = pts, base
            return frame

    sender = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    receiver = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    channel = BrowserCameraChannel(CameraConfig(index=0, width=160, height=120, target_fps=30))
    source = channel.new_connection()
    received = 0
    task = None
    @receiver.on("track")
    def on_track(track):
        async def consume():
            nonlocal received
            while True:
                frame = await track.recv()
                source.offer(frame.to_ndarray(format="bgr24"))
                received += 1
        nonlocal task
        task = asyncio.create_task(consume())
    report = {}
    with tempfile.TemporaryDirectory(prefix="browser-transport-", dir=Path(__file__).resolve().parents[1] / "tmp") as directory:
        path = str(Path(directory) / "transport.webm")
        try:
            sender.addTrack(SyntheticTrack())
            await sender.setLocalDescription(await sender.createOffer())
            await receiver.setRemoteDescription(sender.localDescription)
            await receiver.setLocalDescription(await receiver.createAnswer())
            await sender.setRemoteDescription(receiver.localDescription)
            deadline = time.monotonic()+10
            while not source.ready and time.monotonic()<deadline:
                await asyncio.sleep(.05)
            if not source.ready:
                raise RuntimeError("Local WebRTC peer connection did not deliver frames")
            channel.preview_ai_enabled = True
            if not channel.start(path, "synthetic-browser-video") or not channel.is_recording:
                raise RuntimeError("Browser recording writer unavailable")
            await asyncio.sleep(2)
            stats = await asyncio.to_thread(channel.stop_recording, True)
            source.release()
            await asyncio.to_thread(channel.release)
            capture = cv2.VideoCapture(path)
            ok, frame = capture.read()
            capture.release()
            report = {"passed": bool(ok and frame is not None and stats["frames_written"] > 10),
                      "received_webrtc_frames": received, "recorded_frames": stats["frames_written"],
                      "recording_decodes": bool(ok), "capture_thread_stopped": not channel.is_live,
                      "mode": "Two local aiortc peers, synthetic images, real WebM encoder/decoder; no camera, cloud or TURN test"}
        finally:
            source.release()
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await sender.close()
            await receiver.close()
            await asyncio.to_thread(channel.release)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    (Path(__file__).resolve().parents[1] / "tmp").mkdir(exist_ok=True)
    report = asyncio.run(asyncio.wait_for(verify(), timeout=30))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    sys.exit(0 if report["passed"] else 1)
