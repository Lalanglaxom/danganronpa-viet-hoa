' run_cli.vbs - Launch DR PO Toolkit CLI help in a console
' Put this file in the same folder as run_cli.py.

Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")

strScriptFolder = objFSO.GetParentFolderName(WScript.ScriptFullName)
objShell.CurrentDirectory = strScriptFolder

If objFSO.FileExists(strScriptFolder & "\run_cli.py") Then
    objShell.Run "cmd /k python run_cli.py --help", 1, False
Else
    MsgBox "Cannot find run_cli.py in:" & vbCrLf & strScriptFolder, vbCritical, "DR PO Toolkit"
End If
