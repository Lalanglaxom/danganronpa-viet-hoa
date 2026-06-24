Option Explicit

' run_toolkit.vbs - Launch DR PO Toolkit GUI.

Dim shell, fso, folder, pythonExe, scriptPath, launchCmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

folder = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = folder

scriptPath = folder & "\run_toolkit.py"
If Not fso.FileExists(scriptPath) Then
    MsgBox "Cannot find run_toolkit.py in:" & vbCrLf & folder, vbCritical, "DR PO Toolkit"
    WScript.Quit 1
End If

pythonExe = folder & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pythonExe) Then
    pythonExe = folder & "\.venv\Scripts\python.exe"
End If

If Not fso.FileExists(pythonExe) Then
    MsgBox "Cannot find a Python launcher in:" & vbCrLf & folder & "\.venv\Scripts", vbCritical, "DR PO Toolkit"
    WScript.Quit 1
End If

launchCmd = "cmd /c cd /d " & Q(folder) & " && " & Q(pythonExe) & " " & Q(scriptPath)
shell.Run launchCmd, 0, False

Function Q(ByVal s)
    Q = Chr(34) & s & Chr(34)
End Function
