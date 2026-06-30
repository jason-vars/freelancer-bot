' ============================================================
'  freelancer-bot - START (double-click, no console window)
'  Runs the polling loop + web UI hidden in the background via
'  pythonw.exe, then opens the dashboard in your browser.
'  To stop it later, double-click stop-bot.vbs.
'  Logs go to bot.log in this folder.
' ============================================================
Option Explicit
Dim sh, fso, base, py, exe
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

sh.CurrentDirectory = base
' Window style 0 = hidden, False = don't wait for it to finish.
exe = """" & py & """ -m bot.cli serve"
sh.Run exe, 0, False

' Give the web server a moment to bind, then open the dashboard.
WScript.Sleep 2500
sh.Run "http://127.0.0.1:8765", 1, False
