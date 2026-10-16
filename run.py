import os
import sys
import subprocess
from dotenv import load_dotenv

os.chdir(os.path.dirname(os.path.abspath(__file__)))
print('Starting WhisperWriter...')
load_dotenv()
sys.exit(subprocess.run([sys.executable, os.path.join('src', 'main.py')] + sys.argv[1:]).returncode)
