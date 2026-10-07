import os
import uvicorn

if __name__ == '__main__':
    from app.config import load_environment
    load_environment()
    import platform
    if 'microsoft' not in platform.release().lower():
        raise RuntimeError('Huella runs in WSL2 and uses dedicated Windows Chrome.')
    port=int(os.getenv('HUELLA_PORT','8765'))
    if not 1024 <= port <= 65535:
        raise ValueError('HUELLA_PORT debe estar entre 1024 y 65535.')
    reload_enabled=os.getenv('HUELLA_RELOAD','0')=='1'
    options={'reload':True,'reload_dirs':['app']} if reload_enabled else {}
    uvicorn.run('app.main:app', host='127.0.0.1', port=port, loop='asyncio', **options)
