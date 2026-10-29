' Agentni KO'RINMAS holda ishga tushiradi.
' PowerShell'ni to'g'ridan-to'g'ri vazifa rejalashtiruvchidan chaqirsak,
' har safar qora oyna "chaqnab" o'tadi — kompyuterda ishlayotgan odamga
' xalaqit beradi. Bu yerda esa oyna umuman yaratilmaydi (Run ... , 0).
Dim sh, fso, base
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File """ & base & "\agent.ps1""", 0, False
