from services.celery_tasks import app

worker_options = [
    "worker",
    "--loglevel=INFO",
    "--hostname=castora_worker@%h",
    "--pool=solo",
]

if __name__ == "__main__":
    print("Starting Castora podcast worker...")
    print("Registered tasks:", sorted(name for name in app.tasks.keys() if name.startswith("services.")))
    app.worker_main(worker_options)
