' run_toolkit.vbs - Launch DR PO Toolkit GUI
' Put this file in the same folder as run_toolkit.py.

Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")

' Get the folder where this script is located
strScriptFolder = objFSO.GetParentFolderName(WScript.ScriptFullName)
objShell.CurrentDirectory = strScriptFolder

' Prefer pythonw.exe so no console window appears.
' Fallback to python.exe if pythonw is unavailable.
If objFSO.FileExists(strScriptFolder & "\run_toolkit.py") Then
    objShell.Run "cmd /c where pythonw >nul 2>nul && pythonw run_toolkit.py || python run_toolkit.py", 0, False
Else
    MsgBox "Cannot find run_toolkit.py in:" & vbCrLf & strScriptFolder, vbCritical, "DR PO Toolkit"
End If

' Chỉ là một cái thùng rác.
' Nhưng bên trong chẳng có rác.
' Nếu không có rác... liệu nó có phải là một cái thùng rác không?
