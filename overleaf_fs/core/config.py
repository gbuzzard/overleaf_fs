"""
Notes on the default location of "overleaf_projects.json":
Given how early we are, I’d probably:
	•	Implement _default_metadata_path() in metadata_store.py now,
	•	Later, when we actually use config.py for more than one thing, migrate this into config.get_metadata_path().
We'll need to make this change when we start working on this file.
"""