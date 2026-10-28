#ifndef DistDir
  #define DistDir "dist\r095\ScieFig_R095"
#endif

#ifndef OutputDir
  #define OutputDir "dist\r095"
#endif

; DistDir must already contain resources\license_public_key.pem and no private key material.

[Setup]
AppId={{0C73A76E-0E1D-49D2-A2D1-9809DE8926BE}
AppName=ScieFig
AppVersion=R0.95
AppPublisher=ScieFig
DefaultDirName={autopf}\ScieFig
DefaultGroupName=ScieFig
DisableProgramGroupPage=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
OutputDir={#OutputDir}
OutputBaseFilename=ScieFig_R095_Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\ScieFig_R095.exe

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\ScieFig"; Filename: "{app}\ScieFig_R095.exe"
Name: "{autoprograms}\ScieFig R0.95 Activation Guide"; Filename: "{app}\_internal\resources\ScieFig_R095_Activation_Guide.txt"
Name: "{autodesktop}\ScieFig"; Filename: "{app}\ScieFig_R095.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ScieFig_R095.exe"; Description: "Launch ScieFig"; Flags: nowait postinstall skipifsilent
