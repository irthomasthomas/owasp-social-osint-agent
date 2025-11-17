# In a new tasks.py
from celery import Celery
from socialosintagent.analyzer import SocialOSINTAgent
# ... setup agent instance ...

celery_app = Celery('tasks', broker='redis://redis:6379/0')

@celery_app.task
def run_osint_analysis(platforms, query):
    """This is the function the background worker will run."""
    result = agent.analyze(platforms, query)
    return result