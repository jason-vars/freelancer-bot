' ============================================================
'  freelancer-bot - STOP (double-click)
'  Stops the background bot started by start-bot.vbs, using the
'  process id recorded in bot.pid.
' ============================================================
Option Explicit
Dim sh, fso, base, pidFile, f, pid

Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

base    = fso.GetParentFolderName(WScript.ScriptFullName)
pidFile = base & "\bot.pid"

If Not fso.FileExists(pidFile) Then
    MsgBox "No running bot found (bot.pid is missing)." & vbCrLf & _
           "It may already be stopped.", vbExclamation, "Freelancer Bot"
    WScript.Quit 0
End If

Set f = fso.OpenTextFile(pidFile, 1)
pid = Trim(f.ReadAll)
f.Close

If pid = "" Then
    fso.DeleteFile pidFile
    MsgBox "bot.pid was empty - nothing to stop.", vbExclamation, "Freelancer Bot"
    WScript.Quit 0
End If

' /T also kills child processes, /F forces it. Run hidden, wait for it.
sh.Run "taskkill /PID " & pid & " /T /F", 0, True

On Error Resume Next
fso.DeleteFile pidFile
On Error Goto 0

MsgBox "Bot stopped.", vbInformation, "Freelancer Bot"
