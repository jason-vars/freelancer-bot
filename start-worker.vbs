' ============================================================
'  freelancer-bot - START WORKER for the web app (double-click,
'  no console window). Same hidden background process as
'  start-bot.vbs, but for cloud mode: it fetches jobs into
'  Supabase for the website and does not open the local
'  dashboard (the website's Admin page shows its status).
'  To stop it, double-click stop-bot.vbs.
'  Logs go to bot.log in this folder.
' ============================================================
Option Explicit
Dim sh, fso, base, py, envText, f

Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

base = fso.GetParentFolderName(WScript.ScriptFullName)
py   = base & "\.venv\Scripts\pythonw.exe"

If Not fso.FileExists(py) Then
    MsgBox "Python environment not found:" & vbCrLf & py & vbCrLf & vbCrLf & _
           "Run setup.bat first.", vbCritical, "Freelancer Bot"
    WScript.Quit 1
End If
If Not fso.FileExists(base & "\.env") Then
    MsgBox "No .env file found in this folder. Create it before running.", _
           vbExclamation, "Freelancer Bot"
    WScript.Quit 1
End If

' Without both CLOUD_* keys the bot runs locally only and the website gets no jobs.
Set f = fso.OpenTextFile(base & "\.env", 1)
envText = vbLf & Replace(f.ReadAll, vbCr, "")
f.Close
If InStr(envText, vbLf & "CLOUD_SUPABASE_URL=http") = 0 Or _
   (InStr(envText, vbLf & "CLOUD_SUPABASE_KEY=sb_") = 0 And InStr(envText, vbLf & "CLOUD_SUPABASE_KEY=ey") = 0) Then
    MsgBox "CLOUD_SUPABASE_URL and CLOUD_SUPABASE_KEY are not set in .env." & vbCrLf & vbCrLf & _
           "Add them (see web/README.md, step 4), otherwise jobs never reach the website.", _
           vbExclamation, "Freelancer Bot"
    WScript.Quit 1
End If

sh.CurrentDirectory = base
' Window style 0 = hidden, False = don't wait for it to finish. "serve" refuses to
' start a second copy, so double-clicking twice is harmless.
sh.Run """" & py & """ -m bot.cli serve", 0, False
