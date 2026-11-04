#ifndef DistDir
  #define DistDir "dist\r10\ScieFig_R10"
#endif

#ifndef OutputDir
  #define OutputDir "dist\r10"
#endif

#ifndef IconFile
  #define IconFile "..\resources\icons\sciefig_app_icon.ico"
#endif

; DistDir must already contain resources\license_public_key.pem and no private key material.

[Setup]
AppId={{0C73A76E-0E1D-49D2-A2D1-9809DE8926BE}
AppName=ScieFig
AppVersion=1.2.2
AppPublisher=ScieFig
LicenseFile=..\LICENSE
DefaultDirName={autopf}\ScieFig
DefaultGroupName=ScieFig
DisableProgramGroupPage=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
OutputDir={#OutputDir}
OutputBaseFilename=ScieFig_1.2.2_Setup
Compression=lzma2/ultra64
LZMANumBlockThreads=2
LZMAUseSeparateProcess=yes
SolidCompression=yes
WizardStyle=modern
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\ScieFig_R10.exe
ChangesAssociations=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
#include FilesList

[Icons]
Name: "{autoprograms}\ScieFig"; Filename: "{app}\ScieFig_R10.exe"
Name: "{autoprograms}\ScieFig Getting Started"; Filename: "{app}\_internal\resources\ScieFig_R10_Activation_Guide.txt"
Name: "{autodesktop}\ScieFig"; Filename: "{app}\ScieFig_R10.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ScieFig_R10.exe"; Description: "Launch ScieFig"; Flags: nowait postinstall skipifsilent

[Registry]
Root: HKA; Subkey: "Software\Classes\.sfig\OpenWithProgids"; ValueType: string; ValueName: "ScieFig.Project"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\ScieFig.Project"; ValueType: string; ValueName: ""; ValueData: "ScieFig Project"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\ScieFig.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\ScieFig_R10.exe,0"
Root: HKA; Subkey: "Software\Classes\ScieFig.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\ScieFig_R10.exe"" ""%1"""
Root: HKA; Subkey: "Software\Classes\Applications\ScieFig_R10.exe\SupportedTypes"; ValueType: string; ValueName: ".sfig"; ValueData: ""; Flags: uninsdeletevalue
