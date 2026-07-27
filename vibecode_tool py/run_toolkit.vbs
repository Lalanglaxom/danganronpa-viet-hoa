Option Explicit

' run_toolkit.vbs - Launch DR PO Toolkit GUI and offer to install missing GUI requirements.

Dim shell, fso, folder, pythonExe, pythonConsole, scriptPath, requirementsPath
Dim launchCmd, checkCmd, installCmd, checkExit, answer
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

folder = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = folder

scriptPath = folder & "\run_toolkit.py"
requirementsPath = folder & "\requirements.txt"
If Not fso.FileExists(scriptPath) Then
    MsgBox "Cannot find run_toolkit.py in:" & vbCrLf & folder, vbCritical, "DR PO Toolkit"
    WScript.Quit 1
End If

pythonConsole = folder & "\.venv\Scripts\python.exe"
If Not fso.FileExists(pythonConsole) Then
    MsgBox "Cannot find the toolkit Python environment in:" & vbCrLf & folder & "\.venv\Scripts" & vbCrLf & vbCrLf & _
        "Create it first with:" & vbCrLf & "py -m venv .venv", vbCritical, "DR PO Toolkit"
    WScript.Quit 1
End If

pythonExe = folder & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pythonExe) Then
    pythonExe = pythonConsole
End If

checkCmd = Q(pythonConsole) & " -c " & Q("import PyQt6")
checkExit = shell.Run(checkCmd, 0, True)
If checkExit <> 0 Then
    answer = MsgBox("PyQt6 is missing from this toolkit environment." & vbCrLf & vbCrLf & _
        "Install requirements now? A Command Prompt will open and show progress.", _
        vbYesNo + vbQuestion, "DR PO Toolkit setup")
    If answer = vbYes Then
        If Not fso.FileExists(requirementsPath) Then
            MsgBox "Cannot find requirements.txt in:" & vbCrLf & folder, vbCritical, "DR PO Toolkit"
            WScript.Quit 1
        End If
        installCmd = "cmd /k cd /d " & Q(folder) & " && " & Q(pythonConsole) & _
            " -m pip install -r " & Q(requirementsPath) & _
            " && echo. && echo Installation complete. Close this window, then launch the toolkit again."
        shell.Run installCmd, 1, False
    End If
    WScript.Quit 1
End If

launchCmd = "cmd /c cd /d " & Q(folder) & " && " & Q(pythonExe) & " " & Q(scriptPath)
shell.Run launchCmd, 0, False

Function Q(ByVal s)
    Q = Chr(34) & s & Chr(34)
End Function
