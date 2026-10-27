from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TencentDeploymentTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_caddy_serves_http_without_certificate_issuance(self):
        config = self.read("deploy/tencent/Caddyfile")
        self.assertIn(":80 {", config)
        self.assertNotIn("issuer acme", config)

    def test_compose_exposes_only_the_public_reverse_proxy(self):
        compose = self.read("deploy/tencent/compose.yml")
        self.assertIn("caddy:2.11.7-alpine", compose)
        self.assertIn('"8000:80"', compose)
        self.assertNotIn('"443:443"', compose)
        self.assertNotIn('"8000:8000"', compose)

    def test_deploy_normalizes_old_ip_configuration(self):
        script = self.read("deploy/tencent/deploy.sh")
        self.assertIn('value="${value#http://}"', script)
        self.assertIn('value="${value#https://}"', script)
        self.assertIn('upsert_env "MPT_COOKIE_SECURE" "0"', script)
        self.assertIn('http://127.0.0.1:8000/api/health', script)
        self.assertIn("openssl rand -base64 32", script)

    def test_first_deploy_and_update_share_the_same_entrypoint(self):
        first = self.read("deploy/tencent/first-deploy-ip.sh")
        update = self.read("deploy/tencent/update.sh")
        self.assertIn('MAPLE_SITE_ADDRESS="${PUBLIC_IP}"', first)
        self.assertIn("deploy/tencent/deploy.sh", first)
        self.assertIn("</dev/tty", first)
        self.assertIn('export MAPLE_SKIP_GIT_SYNC=1', first)
        self.assertIn("deploy/tencent/deploy.sh", update)
        self.assertIn('MAPLE_SKIP_GIT_SYNC=1', update)

    def test_documentation_describes_http(self):
        guide = self.read("docs/腾讯云部署.md")
        self.assertIn("http://公网IP:8000/", guide)
        self.assertIn("first-deploy-ip.sh", guide)
        self.assertNotIn("edge://net-internals", guide)


if __name__ == "__main__":
    unittest.main()
