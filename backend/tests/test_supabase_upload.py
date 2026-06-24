import unittest

import pandas as pd

from app.supabase_upload import (
    UploadValidationError,
    prepare_dataframe,
    rename_uploaded_columns,
)


class SupabaseUploadPreparationTests(unittest.TestCase):
    def test_applies_user_column_renames(self) -> None:
        frame = pd.DataFrame({"ticket_id": [1], "old_status": ["Open"]})

        result = rename_uploaded_columns(
            frame,
            preview_names=["ticket_id", "old_status"],
            requested_names=["ticket_id", "status"],
        )

        self.assertEqual(result.columns.tolist(), ["ticket_id", "status"])

    def test_rejects_null_in_non_nullable_column(self) -> None:
        frame = pd.DataFrame({"ticket_id": [1, 2], "status": ["Open", None]})

        with self.assertRaisesRegex(UploadValidationError, "status.*NULL"):
            prepare_dataframe(
                frame,
                [
                    {"name": "ticket_id", "type": "integer", "nullable": False},
                    {"name": "status", "type": "text", "nullable": False},
                ],
            )

    def test_reports_invalid_integer_rows(self) -> None:
        frame = pd.DataFrame({"ticket_id": ["1", "not-an-id"]})

        with self.assertRaisesRegex(UploadValidationError, "ticket_id.*3"):
            prepare_dataframe(
                frame,
                [{"name": "ticket_id", "type": "integer", "nullable": False}],
            )

    def test_coerces_valid_values_to_selected_types(self) -> None:
        frame = pd.DataFrame(
            {"ticket_id": ["1", "2"], "active": ["true", "false"]}
        )

        result = prepare_dataframe(
            frame,
            [
                {"name": "ticket_id", "type": "integer", "nullable": False},
                {"name": "active", "type": "boolean", "nullable": False},
            ],
        )

        self.assertEqual(result["ticket_id"].tolist(), [1, 2])
        self.assertEqual(result["active"].tolist(), [True, False])

    def test_rejects_primary_keys_that_collide_after_type_coercion(self) -> None:
        frame = pd.DataFrame({"ticket_id": ["1", "01"]})

        with self.assertRaisesRegex(UploadValidationError, "primary key.*duplicate"):
            prepare_dataframe(
                frame,
                [{"name": "ticket_id", "type": "integer", "nullable": False}],
                primary_keys=["ticket_id"],
            )


if __name__ == "__main__":
    unittest.main()
