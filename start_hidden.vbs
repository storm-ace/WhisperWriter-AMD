' WhisperWriter - start without a console window (pythonw.exe from the local .venv).
' Arguments are passed on, e.g. --autostart for the login shortcut.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = scriptDir
args = ""
For Each a In WScript.Arguments
    args = args & " " & a
Next
sh.Run """" & scriptDir & "\.venv\Scripts\pythonw.exe"" run.py" & args, 0, False
