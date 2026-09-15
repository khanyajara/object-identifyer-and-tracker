# Streamlit Cloud startup: OpenCV ImportError

## Confirmed follow-up from deployment logs

The supplied deployment log confirms Debian Trixie installed `libgl1` successfully. Startup then failed with `ImportError: libgthread-2.0.so.0: cannot open shared object file`. The root `packages.txt` now also includes `libglib2.0-0t64`, which supplies that library on Trixie. See the [Debian package file list](https://packages.debian.org/trixie/amd64/libglib2.0-0t64/filelist). Rebuild and verify startup after this additional dependency is installed; successful recovery is not yet confirmed.

The reported traceback fails while loading OpenCV's native module, before application startup. The exception text is redacted, so the missing library cannot be identified conclusively without the deployment log.

This repository uses `opencv-python`, which is also required by Ultralytics. The root `packages.txt` now includes `libgl1` so Community Cloud installs the Linux library needed for `libGL.so.1`. This follows [Streamlit's OpenCV guidance](https://docs.streamlit.io/knowledge-base/dependencies/libgl).

## Apply the fix

1. Commit and push `packages.txt` and the requirements comment to the GitHub branch used by the deployed app.
2. Let Community Cloud rebuild the dependencies. Check the build log to confirm it installs `libgl1`. Reboot the app from Manage app if needed.
3. Confirm startup reaches the sign-in page.
4. If import still fails, inspect the full exception in Manage app logs. Share only the final ImportError line, with any sensitive values removed, to identify the remaining dependency.

Keep `packages.txt` at the repository root. Do not install headless and regular OpenCV together: both supply the same `cv2` module, and Ultralytics requires regular OpenCV in this dependency set.

The supplied cloud traceback shows Python 3.14, while `runtime.txt` contains `python-3.12`. Check the Python version selected in Cloud deployment settings; the traceback alone does not establish a Python-version incompatibility. See [Streamlit dependency configuration](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies).

This configuration fix has not been verified inside the user's deployed Linux environment. A successful Windows import does not validate Linux shared-library availability.
