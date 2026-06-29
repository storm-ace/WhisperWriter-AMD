' WhisperWriter — stille autostart (geen console-venster).
' Start run.py via pythonw.exe uit de lokale venv (.venv), onafhankelijk van waar de repo staat.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = scriptDir
sh.Run """" & scriptDir & "\.venv\Scripts\pythonw.exe"" run.py", 0, False
