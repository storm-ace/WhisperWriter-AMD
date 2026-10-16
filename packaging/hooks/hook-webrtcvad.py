# Replaces the contrib hook, which copies metadata of the "webrtcvad" distribution.
# This project installs the same module from the "webrtcvad-wheels" distribution.
from PyInstaller.utils.hooks import copy_metadata

datas = copy_metadata('webrtcvad-wheels')
