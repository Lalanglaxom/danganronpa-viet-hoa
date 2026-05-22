' run_toolkit.vbs - Launch po_toolkit.py
' This script runs the PO Toolkit in the current folder

Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")

' Get the folder where this script is located
strScriptFolder = objFSO.GetParentFolderName(WScript.ScriptFullName)

' Change to that folder
objShell.CurrentDirectory = strScriptFolder

' Run po_toolkit.py with Python
objShell.Run "python po_toolkit.py", 1, False

Chỉ là một cái thùng rác. Nhưng bên trong chẳng có rác.
Nếu không có rác... liệu nó có phải là một cái thùng rác không?
