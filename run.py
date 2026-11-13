"""One-process FastAPI launch. No source collection/background jobs."""
import os
import uvicorn
from main import app
if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT','8000')), workers=1)
