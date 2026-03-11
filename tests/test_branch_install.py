#!/usr/bin/env python3
"""
安装器测试。
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dreo_branch_install as installer


class BranchInstallTest(unittest.TestCase):
    def make_args(self, root: Path) -> argparse.Namespace:
        source = root / "workspace" / "dreo_branch_manager.py"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("#!/usr/bin/env python3\nprint('v1')\n", encoding="utf-8")
        return argparse.Namespace(
            action=None,
            home=root / "home",
            source=source,
            install_dir=None,
            bin_dir=None,
        )

    def test_update_overwrites_installed_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = self.make_args(root)

            installer.install_or_update(args, installer.ACTION_INSTALL)
            paths = installer.resolve_paths(args)
            self.assertEqual(
                paths["target_script"].read_text(encoding="utf-8"),
                "#!/usr/bin/env python3\nprint('v1')\n",
            )

            args.source.write_text("#!/usr/bin/env python3\nprint('v2')\n", encoding="utf-8")
            installer.install_or_update(args, installer.ACTION_UPDATE)

            self.assertEqual(
                paths["target_script"].read_text(encoding="utf-8"),
                "#!/usr/bin/env python3\nprint('v2')\n",
            )

    def test_uninstall_removes_installed_files_but_keeps_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = self.make_args(root)

            installer.install_or_update(args, installer.ACTION_INSTALL)
            paths = installer.resolve_paths(args)

            zshrc = paths["home"] / ".zshrc"
            self.assertTrue(paths["target_script"].exists())
            self.assertTrue((paths["bin_dir"] / "dreo_branch_manager").exists())
            self.assertIn(installer.INSTALL_MARKER, zshrc.read_text(encoding="utf-8"))

            installer.uninstall(args)

            self.assertTrue(args.source.exists())
            self.assertFalse(paths["target_script"].exists())
            self.assertFalse((paths["bin_dir"] / "dreo_branch_manager").exists())
            self.assertNotIn(installer.INSTALL_MARKER, zshrc.read_text(encoding="utf-8"))

    def test_activation_command_matches_shell(self) -> None:
        bin_dir = Path("/tmp/demo-bin")
        with mock.patch.dict(os.environ, {"SHELL": "/bin/zsh"}, clear=False):
            self.assertEqual(
                installer.activation_command(bin_dir),
                'export PATH="/tmp/demo-bin:$PATH"',
            )
        with mock.patch.dict(os.environ, {"SHELL": "/opt/homebrew/bin/fish"}, clear=False):
            self.assertEqual(
                installer.activation_command(bin_dir),
                'set -gx PATH "/tmp/demo-bin" $PATH',
            )


if __name__ == "__main__":
    unittest.main()
