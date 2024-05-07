import io
from pathlib import Path

import pytest

from ip_explorer import create_app

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / 'fixtures'
BLUECAT_SAMPLE = FIXTURES / 'bluecat_sample.csv'
RUNZERO_SAMPLE = FIXTURES / 'runzero_sample.csv'


def make_app(db_path, **config):
    return create_app({'TESTING': True, 'DATABASE': str(db_path), **config})


@pytest.fixture
def app(tmp_path):
    """A fresh, empty database, as on first start."""
    return make_app(tmp_path / 'explorer.sqlite')


@pytest.fixture
def client(app):
    return app.test_client()


def upload(client, kind, data, name):
    return client.post('/api/import/upload', data={'kind': kind, 'file': (io.BytesIO(data), name)},
                       content_type='multipart/form-data').get_json()


@pytest.fixture
def loaded(client):
    """The sample BlueCat and runZero files uploaded and committed through the API."""
    bc = upload(client, 'bc', BLUECAT_SAMPLE.read_bytes(), 'bluecat_sample.csv')
    rz = upload(client, 'rz', RUNZERO_SAMPLE.read_bytes(), 'runzero_sample.csv')
    client.post('/api/import/commit', json={'rz': rz['uploadId'], 'bc': bc['uploadId']})
    return client
