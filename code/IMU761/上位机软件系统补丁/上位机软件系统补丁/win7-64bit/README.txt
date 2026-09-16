
64位Win7运行

1.将MSSTDFMT.dll，comdlg32.ocx,mscomctl.ocx,mscomm32.ocx拷贝至C:\windows\Syswow64\目录下。

2.一定要以管理员的身份运行CMD.exe,(注意：不是在开始->运行->cmd，也不能win+R快捷键调用，
而是通过搜索方式(随便你用什么方式...)找到CMD.exe文件，右击以管理员身份运行！切记切记！成功关键在此！
*CMD.EXE C:\windows\Syswow64\目录下有.

3.在CMD窗口输入命令：cd C:\windows\Syswow64\,然后依次执行下列命令：
regsvr32 MSSTDFMT.dll
regsvr32 comdlg32.ocx
regsvr32 mscomctl.ocx
regsvr32 mscomm32.ocx
