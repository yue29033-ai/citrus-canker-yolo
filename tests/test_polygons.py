"""Polygon validation and export use synthetic, disposable images only."""
import csv
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from PIL import Image
from canker_workbench import polygons as p


LEAF = {"label": "leaf", "points": [[1,1],[18,1],[18,18],[1,18]]}
LESION = {"label": "canker", "points": [[4,4],[8,4],[8,8],[4,8]]}


def fixture(root):
    rows = []
    for split, color in [("train", "green"), ("val", "yellow")]:
        path = root / "segmentation_pilot/images" / split / (split + ".png")
        path.parent.mkdir(parents=True)
        Image.new("RGB", (20,20), color).save(path)
        rows.append({"filename":path.name, "original_split":split, "sha256":p.sha256(path)})
    with (root / "segmentation_pilot/selection.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    return p.samples(root)


class PolygonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.rows = fixture(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def complete(self, same_leaf=False):
        for row in self.rows:
            p.save_annotation(self.root,row["id"],[LEAF,LESION],True,"same" if same_leaf else row["id"])

    def test_draft_never_exports_as_negative(self):
        p.save_annotation(self.root,self.rows[0]["id"],[])
        with self.assertRaisesRegex(ValueError,"尚未确认完成"):
            p.export_dataset(self.root)
        self.assertFalse((self.root / "workspace").exists())

    def test_complete_requires_leaf_and_identifier(self):
        for shapes, name in [([],"a"),([LESION],"a"),([LEAF],"")]:
            with self.subTest(shapes=shapes,name=name), self.assertRaises(ValueError):
                p.save_annotation(self.root,self.rows[0]["id"],shapes,True,name)

    def test_invalid_geometry(self):
        for points in [[[1,1],[19,19],[1,19],[15,1]], [[1,1],[1,1],[8,8]],
                       [[1,1],[2,2],[3,3]], [[1,1],[20,3],[5,8]],
                       [[1,1],[float("nan"),3],[5,8]]]:
            with self.subTest(points=points), self.assertRaises(ValueError):
                p.validate_shapes([{"label":"leaf","points":points}],20,20)

    def test_lesion_must_stay_inside_leaf(self):
        with self.assertRaisesRegex(ValueError,"超出了整叶"):
            p.validate_shapes([LEAF,{"label":"canker","points":[[0,0],[5,0],[5,5]]}],20,20,True)

    def test_save_preserves_previous_revision(self):
        row = self.rows[0]
        first = p.save_annotation(self.root,row["id"],[LEAF])
        p.save_annotation(self.root,row["id"],[LEAF,LESION],True,"a")
        history = list((self.root / "segmentation_pilot/annotations/history").glob("*.json"))
        self.assertEqual(len(history),1)
        self.assertEqual(json.loads(history[0].read_text()),first)

    def test_cross_split_leaf_rejected(self):
        self.complete(same_leaf=True)
        with self.assertRaisesRegex(ValueError,"跨集合"):
            p.export_dataset(self.root)
        self.assertFalse((self.root / "workspace").exists())

    def test_changed_image_rejected(self):
        self.complete()
        Image.new("RGB",(20,20),"red").save(self.rows[0]["path"])
        with self.assertRaisesRegex(ValueError,"版本不一致"):
            p.export_dataset(self.root)

    def test_export_class_mapping_and_immutable_versions(self):
        self.complete()
        output = p.export_dataset(self.root)
        config = json.loads((output / "data.yaml").read_text())
        self.assertEqual(config["names"],{"0":"leaf","1":"canker"})
        lines = (output / "labels/train/train.txt").read_text().splitlines()
        self.assertEqual(lines[0],"0 0.05000000 0.05000000 0.90000000 0.05000000 0.90000000 0.90000000 0.05000000 0.90000000")
        self.assertTrue(lines[1].startswith("1 "))
        for row in self.rows:
            self.assertEqual(p.sha256(output / "images" / row["original_split"] / row["filename"]),row["sha256"])
        self.assertNotEqual(p.export_dataset(self.root),output)

    def test_confirmed_negative_still_has_leaf_label(self):
        p.save_annotation(self.root,self.rows[0]["id"],[LEAF],True,"negative-leaf")
        p.save_annotation(self.root,self.rows[1]["id"],[LEAF,LESION],True,"positive-leaf")
        output = p.export_dataset(self.root)
        self.assertEqual(len((output / "labels/train/train.txt").read_text().splitlines()),1)

    def test_restore_pilot_preserves_manifest_and_annotations(self):
        from segmentation_pilot import prepare_pilot
        import shutil
        rows = []
        for row in self.rows:
            source = self.root / "dataset/images" / row["original_split"] / row["filename"]
            source.parent.mkdir(parents=True)
            shutil.move(row["path"],source)
            rows.append({"filename":row["filename"],"original_split":row["original_split"],
                         "sha256":row["sha256"],"source_path":source.relative_to(self.root).as_posix(),"leaf_id":"reviewed"})
        manifest = self.root / "segmentation_pilot/selection.csv"
        with manifest.open("w",newline="") as stream:
            writer = csv.DictWriter(stream,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
        original = manifest.read_bytes()
        selection = {r["original_split"]:[r["filename"]] for r in rows}
        with patch.object(prepare_pilot,"ROOT",self.root), patch.object(prepare_pilot,"PILOT",manifest.parent), patch.object(prepare_pilot,"SELECTION",selection):
            prepare_pilot.main()
            p.save_annotation(self.root,self.rows[0]["id"],[LEAF])
            annotation = p.annotation_path(self.root,self.rows[0]).read_bytes()
            prepare_pilot.main()
        self.assertEqual(manifest.read_bytes(),original)
        self.assertEqual(p.annotation_path(self.root,self.rows[0]).read_bytes(),annotation)
