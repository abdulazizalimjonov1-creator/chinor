' Chinor sennik agentini KORINMAS holda ishga tushiradi.
' PowerShell togridan-togri chaqirilsa, har safar qora oyna chaqnab
' otadi va kompyuterdagi odamga xalaqit beradi. Bu yerda oyna umuman
' yaratilmaydi (Run ..., 0, False).
Dim sh, fso, base
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File """ & base & "\agent.ps1""", 0, False
