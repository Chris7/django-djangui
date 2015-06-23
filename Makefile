testenv:
	pip install -r requirements.txt
	pip install Django
	pip install -e .

test:
	nosetests --with-coverage --cover-erase --cover-package=djangui tests
	coverage run --append --branch --source=djangui --omit=djangui/conf*,djangui/migrations*,djangui/tests*,djangui/backend/ast* `which django-admin.py` test --settings=djangui.test_settings djangui.tests
	coverage report
