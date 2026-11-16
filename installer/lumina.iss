; installer/lumina.iss
; Instalador do Lumina (Inno Setup 6) -> dist\LuminaSetup-<versao>.exe
;
; Compilar (depois de `python build.py`, que gera dist\Lumina.exe e app.ico):
;     ISCC /DAppVersion=2.3 installer\lumina.iss
; ou simplesmente:  python build.py --instalador
;
; Decisoes:
;  - Instala POR USUARIO ({localappdata}\Programs\Lumina) e sem UAC: o
;    atualizador automatico precisa trocar os arquivos sem pedir senha de
;    administrador. Quem quiser instalar para todos usa /ALLUSERS na linha de
;    comando.
;  - Associacao de arquivos so "Abrir com": o Lumina aparece na lista, mas o
;    leitor de PDF padrao do usuario NAO e trocado. Tomar o .pdf de alguem
;    sem perguntar e o tipo de coisa que faz desinstalar um programa.
;  - Os dados (biblioteca, grifos, notas) ficam em %APPDATA%\Lumina e o
;    desinstalador nao os apaga: reinstalar devolve a estante intacta.

#ifndef AppVersion
  #define AppVersion "0.0"
#endif

#define AppName "Lumina"
#define AppExe "Lumina.exe"
#define AppUrl "https://github.com/DEMTRIOBS/Lumina"

[Setup]
; AppId fixo: e por ele que uma versao nova reconhece a anterior (mesma pasta,
; mesmas tarefas, um unico item em "Aplicativos instalados"). Nunca mude.
AppId={{CBECCFFD-7EA1-43CB-BAB0-C98725F52ADB}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=DEMTRIOBS
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
AppUpdatesURL={#AppUrl}/releases/latest
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
OutputDir=..\dist
OutputBaseFilename=LuminaSetup-{#AppVersion}
SetupIconFile=..\app.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; Avisa o Explorer que as associacoes mudaram (icones e "Abrir com").
ChangesAssociations=yes
; Fecha o Lumina aberto antes de sobrescrever o .exe (tambem no modo
; silencioso usado pelo atualizador).
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "epubpadrao"; Description: "Usar o Lumina como leitor padrao de EPUB"; GroupDescription: "Arquivos:"; Flags: unchecked

[Files]
Source: "..\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Desinstalar o {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; HKA = HKCU na instalacao por usuario (HKLM so com /ALLUSERS).
; --- ProgIDs: um por formato, para o Explorer mostrar "Documento PDF (Lumina)".
Root: HKA; Subkey: "Software\Classes\Lumina.PDF"; ValueType: string; ValueName: ""; ValueData: "Documento PDF (Lumina)"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Lumina.PDF\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"",0"
Root: HKA; Subkey: "Software\Classes\Lumina.PDF\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""
Root: HKA; Subkey: "Software\Classes\Lumina.EPUB"; ValueType: string; ValueName: ""; ValueData: "Livro EPUB (Lumina)"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Lumina.EPUB\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"",0"
Root: HKA; Subkey: "Software\Classes\Lumina.EPUB\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""

; --- "Abrir com": entra na lista sem mexer no valor padrao da extensao.
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "Lumina.PDF"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.epub\OpenWithProgids"; ValueType: string; ValueName: "Lumina.EPUB"; ValueData: ""; Flags: uninsdeletevalue

; --- Applications\Lumina.exe: nome amigavel e tipos aceitos no "Escolher outro aplicativo".
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"",0"
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".pdf"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".epub"; ValueData: ""

; --- Capabilities: faz o Lumina aparecer em Configuracoes > Aplicativos padrao,
; onde o PROPRIO usuario escolhe se quer ele para PDF.
Root: HKA; Subkey: "Software\Lumina\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "{#AppName}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Lumina\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "Leitor de PDF e EPUB para tela dividida"
Root: HKA; Subkey: "Software\Lumina\Capabilities\FileAssociations"; ValueType: string; ValueName: ".pdf"; ValueData: "Lumina.PDF"
Root: HKA; Subkey: "Software\Lumina\Capabilities\FileAssociations"; ValueType: string; ValueName: ".epub"; ValueData: "Lumina.EPUB"
Root: HKA; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "Lumina"; ValueData: "Software\Lumina\Capabilities"; Flags: uninsdeletevalue

; --- Tarefa opcional: EPUB abre no Lumina com duplo clique. Raramente ha outro
; leitor de EPUB instalado; se o usuario ja escolheu um (UserChoice), o
; Windows mantem a escolha dele, como deve ser.
Root: HKA; Subkey: "Software\Classes\.epub"; ValueType: string; ValueName: ""; ValueData: "Lumina.EPUB"; Flags: uninsdeletevalue; Tasks: epubpadrao

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
