import unittest

from code_reviewer import diffview

SAMPLE = """diff --git a/app.py b/app.py
index 111..222 100644
--- a/app.py
+++ b/app.py
@@ -1,3 +1,4 @@
 def get(user_id):
-    return db.run("SELECT 1")
+    query = "SELECT * FROM t WHERE id=" + user_id
+    return db.run(query)
 # end
diff --git a/package-lock.json b/package-lock.json
index 333..444 100644
--- a/package-lock.json
+++ b/package-lock.json
@@ -1 +1 @@
-{}
+{"changed": true}
diff --git a/old.txt b/new.txt
similarity index 90%
rename from old.txt
rename to new.txt
diff --git a/gone.py b/gone.py
deleted file mode 100644
--- a/gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-x = 1
-y = 2
diff --git a/img.png b/img.png
Binary files a/img.png and b/img.png differ
"""


class DiffViewTests(unittest.TestCase):
    def setUp(self):
        self.files = diffview.parse_unified_diff(SAMPLE)
        diffview.mark_exclusions(self.files, ["package-lock.json", "*.min.js", "vendor/"])

    def test_parses_files_status_and_counts(self):
        by_path = {f.path: f for f in self.files}
        self.assertEqual(set(by_path), {"app.py", "package-lock.json", "new.txt", "gone.py", "img.png"})
        self.assertEqual((by_path["app.py"].added, by_path["app.py"].removed), (2, 1))
        self.assertEqual(by_path["new.txt"].status, "renamed")
        self.assertEqual(by_path["new.txt"].old_path, "old.txt")
        self.assertEqual(by_path["gone.py"].status, "deleted")
        self.assertTrue(by_path["img.png"].binary)

    def test_line_numbers(self):
        hunk = next(f for f in self.files if f.path == "app.py").hunks[0]
        added = [l for l in hunk.lines if l.kind == "+"]
        self.assertEqual([l.new_no for l in added], [2, 3])
        removed = [l for l in hunk.lines if l.kind == "-"]
        self.assertEqual(removed[0].old_no, 2)

    def test_exclusions_and_counts(self):
        self.assertEqual([f.path for f in self.files if f.excluded], ["package-lock.json"])
        self.assertEqual(diffview.changed_line_count(self.files), 3 + 2)  # app.py 3, gone.py 2
        self.assertEqual(diffview.render_excluded(self.files), "package-lock.json")

    def test_is_excluded_patterns(self):
        pats = ["node_modules/", "*.min.js", "go.sum"]
        self.assertTrue(diffview.is_excluded("web/node_modules/x/index.js", pats))
        self.assertTrue(diffview.is_excluded("static/app.min.js", pats))
        self.assertTrue(diffview.is_excluded("svc/go.sum", pats))
        self.assertFalse(diffview.is_excluded("src/app.js", pats))

    def test_directory_patterns_with_several_folders(self):
        plans = ["docs/plans/"]
        self.assertTrue(diffview.is_excluded("docs/plans/step-1.md", plans))
        self.assertTrue(diffview.is_excluded("repo/docs/plans/sub/step-1.md", plans))  # anywhere in the path, like `vendor/`
        self.assertFalse(diffview.is_excluded("docs/plans.md", plans))
        self.assertFalse(diffview.is_excluded("docs/other/plans/x.md", plans))  # the folders must follow each other
        self.assertFalse(diffview.is_excluded("plans/docs/x.md", plans))
        self.assertTrue(diffview.is_excluded("a/vendor/x.go", ["vendor/"]))
        self.assertFalse(diffview.is_excluded("vendor", ["vendor/"]))  # a file named like the folder is not inside it

    def test_render_diff_block_markers(self):
        block = diffview.render_diff_block(self.files)
        self.assertIn("--- app.py (modified)", block)
        self.assertIn("L1   def get(user_id):", block)
        self.assertIn('R2 - ', block)
        self.assertIn('L2 +     query = "SELECT * FROM t WHERE id=" + user_id', block)
        self.assertIn("--- new.txt (renamed from old.txt)", block)
        self.assertNotIn("package-lock.json", block)
        self.assertIn("(binary file, content not shown)", block)

    def test_diff_index(self):
        index = diffview.DiffIndex(self.files)
        self.assertNotIn("package-lock.json", index.paths())
        self.assertEqual(index.changed_ranges("app.py"), [(1, 4)])
        self.assertIn('    return db.run("SELECT 1")', index.removed_texts("app.py"))


if __name__ == "__main__":
    unittest.main()
