' Liga a ponte do WhatsApp e o cardapio SEM janelas. Os textos vao para ponte.log e cardapio.log.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
d = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = d
sh.Environment("Process")("PONTE_OCULTO") = "1"
If fso.FileExists(d & "\ponte_whatsapp\ponte.js") Then
  sh.Run "cmd /c """"" & d & "\iniciar_ponte_whatsapp.bat"" >> """ & d & "\ponte.log"" 2>&1""", 0, False
  WScript.Sleep 3000
End If
sh.Run "cmd /c """"" & d & "\iniciar_cardapio.bat"" >> """ & d & "\cardapio.log"" 2>&1""", 0, False
