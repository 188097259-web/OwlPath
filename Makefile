.PHONY: setup dev start test test-backend test-frontend build doctor audit manifest prepublish service-install service-status service-health service-open service-restart service-update service-logs service-uninstall synthetic-regression synthetic-regression-dry-run synthetic-regression-self-test

setup:
	./scripts/setup.sh

dev:
	./scripts/dev.sh

start:
	./scripts/start.sh

test: test-backend test-frontend build

test-backend:
	cd backend && ../.venv/bin/python -m pytest -q

test-frontend:
	npm --prefix frontend run test

build:
	npm --prefix frontend run build

doctor:
	./scripts/doctor.sh

audit:
	python3 ./scripts/repository_audit.py

manifest:
	python3 ./scripts/build_release_manifest.py

prepublish:
	./scripts/prepublish_audit.sh

service-install:
	./scripts/service.sh install

service-status:
	./scripts/service.sh status

service-health:
	./scripts/service.sh health

service-open:
	./scripts/service.sh open

service-restart:
	./scripts/service.sh restart

service-update:
	./scripts/service.sh update

service-logs:
	./scripts/service.sh logs

service-uninstall:
	./scripts/service.sh uninstall

synthetic-regression-self-test:
	python3 ./scripts/synthetic_regression.py --self-test

synthetic-regression-dry-run:
	python3 ./scripts/synthetic_regression.py --dry-run $(ARGS)

synthetic-regression:
	@test "$(CONFIRM_REAL_API)" = "1" || { \
		echo "未执行：真实模型 API 可能产生费用。请使用 make synthetic-regression CONFIRM_REAL_API=1 [ARGS='...']" >&2; \
		exit 2; \
	}
	python3 ./scripts/synthetic_regression.py --confirm-real-api $(ARGS)
