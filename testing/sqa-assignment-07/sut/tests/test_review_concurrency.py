"""Two independent management sessions must not overwrite the same snapshot."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from conftest import login


def test_concurrent_policy_saves_commit_only_one_shared_revision(app):
    clients = [app.test_client(), app.test_client()]
    tokens = [login(client, 'wenceslas') for client in clients]
    revision = clients[0].get('/api/admin/state').get_json()['policy_revision']
    ready = Barrier(2)

    def save(index):
        ready.wait(timeout=10)
        response = clients[index].put('/api/admin/users/1/roles',
            json={'role_ids': [index + 2], 'base_revision': revision},
            headers={'X-CSRF-Token': tokens[index]})
        return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, range(2)))
    assert sorted(status for status, _ in results) == [200, 409]
    denied = next(data for status, data in results if status == 409)
    assert denied['code'] == 'STALE_POLICY'
    current = clients[0].get('/api/admin/state').get_json()
    assert current['policy_revision'] == revision + 1
    winner = next(index for index, (status, _) in enumerate(results) if status == 200)
    assert current['user_role_map']['1'] == [winner + 2]
