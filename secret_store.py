"""OS protected secrets. Unsupported hosts explicitly use session-only storage."""
import ctypes
import os
from ctypes import wintypes


class SecretBox:
    persistent=os.name=='nt'
    mode='windows_dpapi' if persistent else 'session'

    def _crypt(self,raw,decrypt=False):
        class Blob(ctypes.Structure):
            _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
        buffer=ctypes.create_string_buffer(raw)
        source=Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));target=Blob()
        library=ctypes.WinDLL('crypt32',use_last_error=True)
        if decrypt:
            success=library.CryptUnprotectData(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target))
        else:
            success=library.CryptProtectData(ctypes.byref(source),'Yanshu credentials',None,None,None,1,ctypes.byref(target))
        if not success:raise OSError('OS secret protection failed')
        try:return ctypes.string_at(target.data,target.size)
        finally:
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.LocalFree.argtypes=[ctypes.c_void_p]
            kernel.LocalFree(ctypes.cast(target.data,ctypes.c_void_p))

    def encrypt(self,text):
        return self._crypt(text.encode('utf-8')) if self.persistent else b''

    def decrypt(self,raw):
        return self._crypt(raw,True).decode('utf-8') if self.persistent else ''
