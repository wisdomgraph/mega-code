## First-time bootstrap: make publish-oss-init MSG="Initial OSS release"
publish-oss-init:
	python scripts/publish-oss.py --init --message "$(MSG)"

## Publish main → GitHub fork: make publish-oss MSG="Release v0.1.30: ..."
publish-oss:
	python scripts/publish-oss.py --message "$(MSG)"

## Dry-run (test without pushing): make publish-oss-dry MSG="Test message"
publish-oss-dry:
	python scripts/publish-oss.py --dry-run --message "$(MSG)"

## Sync local public branch to mirror GitHub (run after publish-oss)
sync-public:
	git fetch github main
	git update-ref refs/heads/public FETCH_HEAD
	git push origin public
