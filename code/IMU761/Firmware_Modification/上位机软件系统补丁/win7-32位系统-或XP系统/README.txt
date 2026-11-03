
在WIN7,部分XP 系统没有控件文件的系统 打不开程序。

如果打不开文件,  没有控件文件 按下列步骤注册


1.把下面  mscomm.srg  mscomm32.ocx   mscomm32.dep

     三个文件 复制到系统安装目录（如果安装在 C 盘）
        例如：  XP:    C:/Windows/system32/
                WIN7  32位   C:/Windows/system32
                WIN7  64位   C:/Windows/syswow64  (64位操作系统目录为syswow64)

2.注册 （以管理员身份注册!!!）

      在“运行”中输入：regsvr32 mscomm32.ocx   （手动输入 不要复制）
     或者：程序->附件->命令提示符(右键 管理员身份运行!!!)->  regsvr32 c:\windows\syswow64\mscomm32.ocx  (64位)
                                     regsvr32 c:\windows\system32\mscomm32.ocx  (32位)


       如注册成功，会提示"mscomm32.ocx 中的 DllRegisterServer 成功。"消息。
