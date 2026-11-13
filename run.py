"""One-worker launch keeps snapshot import memory bounded. No background jobs."""
import os
if __name__=='__main__':
    from gunicorn.app.base import BaseApplication
    from main import application
    class Server(BaseApplication):
        def load_config(self):
            for k,v in {'bind':'0.0.0.0:'+os.environ.get('PORT','8000'),'workers':1,'threads':1,'timeout':60,'accesslog':'-','errorlog':'-','limit_request_line':4094,'limit_request_field_size':8190}.items():self.cfg.set(k,v)
        def load(self):return application
    Server().run()
