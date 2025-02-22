import inspect
from decimal import Decimal

from django.db import transaction
from django.db.models import TextField
from django.urls import reverse

import pytest
from rest_framework.status import HTTP_200_OK, HTTP_204_NO_CONTENT

from baserow.contrib.database.fields.dependencies.update_collector import (
    FieldUpdateCollector,
)
from baserow.contrib.database.fields.field_cache import FieldCache
from baserow.contrib.database.fields.field_types import FormulaFieldType
from baserow.contrib.database.fields.fields import BaserowExpressionField
from baserow.contrib.database.fields.handler import FieldHandler
from baserow.contrib.database.fields.models import FormulaField, LookupField
from baserow.contrib.database.fields.registries import field_type_registry
from baserow.contrib.database.formula import (
    BaserowFormulaInvalidType,
    BaserowFormulaNumberType,
    BaserowFormulaTextType,
)
from baserow.contrib.database.formula.ast.tree import BaserowFunctionDefinition
from baserow.contrib.database.formula.registries import formula_function_registry
from baserow.contrib.database.formula.types.exceptions import InvalidFormulaType
from baserow.contrib.database.management.commands.fill_table_rows import fill_table_rows
from baserow.contrib.database.rows.handler import RowHandler
from baserow.contrib.database.table.cache import generated_models_cache
from baserow.contrib.database.views.exceptions import (
    ViewFilterTypeNotAllowedForField,
    ViewSortFieldNotSupported,
)
from baserow.contrib.database.views.handler import ViewHandler
from baserow.contrib.database.views.models import SORT_ORDER_ASC, SORT_ORDER_DESC
from baserow.contrib.database.views.registries import view_filter_type_registry


@pytest.mark.django_db
def test_creating_a_model_with_formula_field_immediately_populates_it(data_fixture):
    table = data_fixture.create_database_table()
    formula_field = data_fixture.create_formula_field(
        table=table, formula="'test'", formula_type="text"
    )
    formula_field_name = f"field_{formula_field.id}"
    model = table.get_model()
    row = model.objects.create()

    assert getattr(row, formula_field_name) == "test"


@pytest.mark.django_db
def test_adding_a_formula_field_to_an_existing_table_populates_it_for_all_rows(
    data_fixture,
):
    user = data_fixture.create_user()
    table = data_fixture.create_database_table(user=user)
    before_model = table.get_model()
    existing_row = before_model.objects.create()
    formula_field = FieldHandler().create_field(
        user, table, "formula", name="formula", formula="'test'"
    )
    formula_field_name = f"field_{formula_field.id}"
    model = table.get_model()
    row = model.objects.create()

    assert getattr(row, formula_field_name) == "test"
    assert getattr(model.objects.get(id=existing_row.id), formula_field_name) == "test"


@pytest.mark.django_db
def test_cant_change_the_value_of_a_formula_field_directly(data_fixture):
    table = data_fixture.create_database_table()
    data_fixture.create_formula_field(
        name="formula", table=table, formula="'test'", formula_type="text"
    )
    data_fixture.create_text_field(name="text", table=table)
    model = table.get_model(attribute_names=True)

    row = model.objects.create(formula="not test")
    assert row.formula == "test"

    row.text = "update other field"
    row.save()

    row.formula = "not test"
    row.save()
    row.refresh_from_db()
    assert row.formula == "test"


@pytest.mark.django_db
def test_get_set_export_serialized_value_formula_field(data_fixture):
    table = data_fixture.create_database_table()
    formula_field = data_fixture.create_formula_field(
        table=table, formula="'test'", formula_type="text"
    )
    formula_field_name = f"field_{formula_field.id}"
    formula_field_type = field_type_registry.get_by_model(formula_field)

    model = table.get_model()
    row_1 = model.objects.create()
    row_2 = model.objects.create()

    old_row_1_value = getattr(row_1, formula_field_name)
    old_row_2_value = getattr(row_2, formula_field_name)

    assert old_row_1_value == "test"
    assert old_row_2_value == "test"

    formula_field_type.set_import_serialized_value(
        row_1,
        formula_field_name,
        formula_field_type.get_export_serialized_value(
            row_1, formula_field_name, {}, None, None
        ),
        {},
        None,
        None,
    )
    formula_field_type.set_import_serialized_value(
        row_2,
        formula_field_name,
        formula_field_type.get_export_serialized_value(
            row_2, formula_field_name, {}, None, None
        ),
        {},
        None,
        None,
    )

    row_1.save()
    row_2.save()

    row_1.refresh_from_db()
    row_2.refresh_from_db()

    assert old_row_1_value == getattr(row_1, formula_field_name)
    assert old_row_2_value == getattr(row_2, formula_field_name)


@pytest.mark.django_db
def test_changing_type_of_other_field_still_results_in_working_filter(data_fixture):
    user = data_fixture.create_user()
    table = data_fixture.create_database_table(user=user)
    grid_view = data_fixture.create_grid_view(user, table=table)
    first_formula_field = data_fixture.create_formula_field(
        table=table, formula="'test'", formula_type="text", name="source"
    )
    formula_field_referencing_first_field = data_fixture.create_formula_field(
        table=table, formula="field('source')", formula_type="text"
    )

    data_fixture.create_view_filter(
        user=user,
        view=grid_view,
        field=formula_field_referencing_first_field,
        type="equal",
        value="t",
    )

    # Change the first formula field to be a boolean field, meaning that the view
    # filter on the referencing formula field is now and invalid and should be deleted
    FieldHandler().update_field(user, first_formula_field, formula="1")

    queryset = ViewHandler().get_queryset(grid_view)
    assert not queryset.exists()
    assert queryset.count() == 0


@pytest.mark.django_db
def test_can_use_complex_date_filters_on_formula_field(data_fixture):
    user = data_fixture.create_user()
    table = data_fixture.create_database_table(user=user)
    grid_view = data_fixture.create_grid_view(user, table=table)
    data_fixture.create_date_field(user=user, table=table, name="date_field")
    formula_field = data_fixture.create_formula_field(
        table=table, formula="field('date_field')", formula_type="date", name="formula"
    )

    data_fixture.create_view_filter(
        user=user,
        view=grid_view,
        field=formula_field,
        type="date_equals_today",
        value="Europe/London",
    )

    queryset = ViewHandler().get_queryset(grid_view)
    assert not queryset.exists()
    assert queryset.count() == 0


@pytest.mark.django_db
def test_can_use_complex_contains_filters_on_formula_field(data_fixture):
    user = data_fixture.create_user()
    table = data_fixture.create_database_table(user=user)
    grid_view = data_fixture.create_grid_view(user, table=table)
    data_fixture.create_date_field(
        user=user, table=table, name="date_field", date_format="US"
    )
    formula_field = data_fixture.create_formula_field(
        table=table,
        formula="field('date_field')",
        formula_type="date",
        name="formula",
        date_format="US",
        date_time_format="24",
    )

    data_fixture.create_view_filter(
        user=user,
        view=grid_view,
        field=formula_field,
        type="contains",
        value="23",
    )

    queryset = ViewHandler().get_queryset(grid_view)
    assert not queryset.exists()
    assert queryset.count() == 0


@pytest.mark.django_db
def test_can_change_formula_type_breaking_other_fields(data_fixture):
    user = data_fixture.create_user()
    table = data_fixture.create_database_table(user=user)
    handler = FieldHandler()
    first_formula_field = handler.create_field(
        user=user, table=table, name="1", type_name="formula", formula="1+1"
    )
    second_formula_field = handler.create_field(
        user=user, table=table, type_name="formula", name="2", formula="field('1')+1"
    )
    assert list(
        second_formula_field.field_dependencies.values_list("id", flat=True)
    ) == [first_formula_field.id]
    assert list(first_formula_field.dependant_fields.values_list("id", flat=True)) == [
        second_formula_field.id
    ]
    assert (
        second_formula_field.dependencies.first().dependency.specific
        == first_formula_field
    )
    handler.update_field(
        user=user, field=first_formula_field, new_type_name="formula", formula="'a'"
    )
    second_formula_field.refresh_from_db()
    assert second_formula_field.formula_type == BaserowFormulaInvalidType.type
    assert "argument number 2" in second_formula_field.error


@pytest.mark.django_db
def test_can_still_insert_rows_with_an_invalid_but_previously_date_formula_field(
    data_fixture,
):
    user = data_fixture.create_user()
    table = data_fixture.create_database_table(user=user)
    handler = FieldHandler()
    date_field = handler.create_field(
        user=user, table=table, name="1", type_name="date"
    )
    formula_field = handler.create_field(
        user=user, table=table, type_name="formula", name="2", formula="field('1')"
    )
    handler.update_field(user=user, field=date_field, new_type_name="single_select")

    row = RowHandler().create_row(user=user, table=table)
    assert getattr(row, f"field_{formula_field.id}") is None


@pytest.mark.django_db
def test_formula_with_row_id_is_populated_after_creating_row(
    data_fixture,
):
    user = data_fixture.create_user()
    table = data_fixture.create_database_table(user=user)
    handler = FieldHandler()
    formula_field = handler.create_field(
        user=user,
        table=table,
        type_name="formula",
        name="2",
        formula="left('abc', row_id())",
    )

    row = RowHandler().create_row(user=user, table=table)
    assert getattr(row, f"field_{formula_field.id}") == "a"


@pytest.mark.django_db
def test_decimal_formula_with_row_id_is_populated_after_creating_row(
    data_fixture,
):
    user = data_fixture.create_user()
    table = data_fixture.create_database_table(user=user)
    handler = FieldHandler()
    formula_field = handler.create_field(
        user=user,
        table=table,
        type_name="formula",
        name="2",
        formula="row_id()/10 * 4",
    )

    row = RowHandler().create_row(user=user, table=table)
    assert getattr(row, f"field_{formula_field.id}") == Decimal("0.40000")


@pytest.mark.django_db
def test_can_rename_field_preserving_whitespace(
    data_fixture,
):
    user = data_fixture.create_user()
    table = data_fixture.create_database_table(user=user)
    handler = FieldHandler()
    test_field = handler.create_field(
        user=user, table=table, type_name="text", name="a"
    )
    formula_field = handler.create_field(
        user=user, table=table, type_name="formula", name="2", formula=" field('a') \n"
    )

    assert formula_field.formula == f" field('a') \n"

    handler.update_field(user=user, field=test_field, name="b")

    formula_field.refresh_from_db()

    assert formula_field.formula == f" field('b') \n"


@pytest.mark.django_db
def test_can_update_lookup_field_value(
    data_fixture, api_client, django_assert_num_queries
):
    user, token = data_fixture.create_user_and_token()
    table = data_fixture.create_database_table(user=user)
    table2 = data_fixture.create_database_table(user=user, database=table.database)
    table_primary_field = data_fixture.create_text_field(
        name="p", table=table, primary=True
    )
    data_fixture.create_text_field(name="primaryfield", table=table2, primary=True)
    looked_up_field = data_fixture.create_date_field(
        name="lookupfield",
        table=table2,
        date_include_time=False,
        date_format="US",
    )

    linkrowfield = FieldHandler().create_field(
        user,
        table,
        "link_row",
        name="linkrowfield",
        link_row_table=table2,
    )

    table2_model = table2.get_model(attribute_names=True)
    a = table2_model.objects.create(lookupfield=f"2021-02-01", primaryfield="primary a")
    b = table2_model.objects.create(lookupfield=f"2022-02-03", primaryfield="primary b")

    table_model = table.get_model(attribute_names=True)

    table_row = table_model.objects.create()
    table_row.linkrowfield.add(a.id)
    table_row.linkrowfield.add(b.id)
    table_row.save()

    formulafield = FieldHandler().create_field(
        user,
        table,
        "formula",
        name="formulafield",
        formula=f"IF(datetime_format(lookup('{linkrowfield.name}',"
        f"'{looked_up_field.name}'), "
        f"'YYYY')='2021', 'yes', 'no')",
    )
    response = api_client.get(
        reverse("api:database:rows:list", kwargs={"table_id": table.id}),
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    assert response.json() == {
        "count": 1,
        "next": None,
        "previous": None,
        "results": [
            {
                f"field_{table_primary_field.id}": None,
                f"field_{linkrowfield.id}": [
                    {"id": a.id, "value": "primary a"},
                    {"id": b.id, "value": "primary b"},
                ],
                f"field_{formulafield.id}": [
                    {"value": "yes", "id": a.id},
                    {"value": "no", "id": b.id},
                ],
                "id": table_row.id,
                "order": "1.00000000000000000000",
            }
        ],
    }
    response = api_client.patch(
        reverse(
            "api:database:rows:item",
            kwargs={"table_id": table2.id, "row_id": a.id},
        ),
        {f"field_{looked_up_field.id}": "2000-02-01"},
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    assert response.status_code == HTTP_200_OK
    response = api_client.get(
        reverse("api:database:rows:list", kwargs={"table_id": table.id}),
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    assert response.json() == {
        "count": 1,
        "next": None,
        "previous": None,
        "results": [
            {
                f"field_{table_primary_field.id}": None,
                f"field_{linkrowfield.id}": [
                    {"id": a.id, "value": "primary a"},
                    {"id": b.id, "value": "primary b"},
                ],
                f"field_{formulafield.id}": [
                    {"value": "no", "id": a.id},
                    {"value": "no", "id": b.id},
                ],
                "id": table_row.id,
                "order": "1.00000000000000000000",
            }
        ],
    }


@pytest.mark.django_db
def test_nested_lookup_with_formula(
    data_fixture, api_client, django_assert_num_queries
):
    user, token = data_fixture.create_user_and_token()
    table = data_fixture.create_database_table(user=user)
    table2 = data_fixture.create_database_table(user=user, database=table.database)
    table3 = data_fixture.create_database_table(user=user, database=table.database)
    table_primary_field = data_fixture.create_text_field(
        name="p", table=table, primary=True
    )
    data_fixture.create_text_field(name="p", table=table3, primary=True)
    data_fixture.create_text_field(name="p", table=table2, primary=True)
    data_fixture.create_text_field(name="lookupfield", table=table2)
    linkrowfield = FieldHandler().create_field(
        user,
        table,
        "link_row",
        name="table_linkrowfield",
        link_row_table=table2,
    )
    linkrowfield2 = FieldHandler().create_field(
        user,
        table2,
        "link_row",
        name="table2_linkrowfield",
        link_row_table=table3,
    )
    table3_model = table3.get_model(attribute_names=True)
    table3_a = table3_model.objects.create(p="table3 a")
    table3_model.objects.create(p="table3 b")
    table3_c = table3_model.objects.create(p="table3 c")
    table3_d = table3_model.objects.create(p="table3 d")
    table2_model = table2.get_model(attribute_names=True)
    table2_1 = table2_model.objects.create(lookupfield=f"lookup 1", p=f"primary 1")
    table2_1.table2linkrowfield.add(table3_a.id)
    table2_1.save()
    table2_2 = table2_model.objects.create(lookupfield=f"lookup 2", p=f"primary 2")
    table2_3 = table2_model.objects.create(lookupfield=f"lookup 3", p=f"primary 3")
    table2_3.table2linkrowfield.add(table3_c.id)
    table2_3.table2linkrowfield.add(table3_d.id)
    table2_3.save()
    table_model = table.get_model(attribute_names=True)
    table1_x = table_model.objects.create(p="table1 x")
    table1_x.tablelinkrowfield.add(table2_1.id)
    table1_x.tablelinkrowfield.add(table2_2.id)
    table1_x.save()
    table1_y = table_model.objects.create(p="table1 y")
    table1_y.tablelinkrowfield.add(table2_3.id)
    table1_y.save()
    # with django_assert_num_queries(1):
    lookup_field = FieldHandler().create_field(
        user,
        table,
        type_name="formula",
        name="formula",
        formula=f"lookup('{linkrowfield.name}','{linkrowfield2.name}')",
    )
    response = api_client.get(
        reverse("api:database:rows:list", kwargs={"table_id": table.id}),
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    assert response.json() == {
        "count": 2,
        "next": None,
        "previous": None,
        "results": [
            {
                f"field_{table_primary_field.id}": table1_x.p,
                f"field_{linkrowfield.id}": [
                    {"id": table2_1.id, "value": table2_1.p},
                    {"id": table2_2.id, "value": table2_2.p},
                ],
                f"field_{lookup_field.id}": [
                    {
                        "value": table3_a.p,
                        "ids": {
                            f"database_table_{table2.id}": table2_1.id,
                            f"database_table_{table3.id}": table3_a.id,
                        },
                    },
                ],
                "id": table1_x.id,
                "order": "1.00000000000000000000",
            },
            {
                f"field_{table_primary_field.id}": table1_y.p,
                f"field_{linkrowfield.id}": [{"id": table2_3.id, "value": table2_3.p}],
                f"field_{lookup_field.id}": [
                    {
                        "value": table3_c.p,
                        "ids": {
                            f"database_table_{table2.id}": table2_3.id,
                            f"database_table_{table3.id}": table3_c.id,
                        },
                    },
                    {
                        "value": table3_d.p,
                        "ids": {
                            f"database_table_{table2.id}": table2_3.id,
                            f"database_table_{table3.id}": table3_d.id,
                        },
                    },
                ],
                "id": table1_y.id,
                "order": "1.00000000000000000000",
            },
        ],
    }


@pytest.mark.django_db
def test_can_delete_lookup_field_value(
    data_fixture, api_client, django_assert_num_queries
):
    user, token = data_fixture.create_user_and_token()
    table = data_fixture.create_database_table(user=user)
    table2 = data_fixture.create_database_table(user=user, database=table.database)
    table_primary_field = data_fixture.create_text_field(
        name="p", table=table, primary=True
    )
    data_fixture.create_text_field(name="primaryfield", table=table2, primary=True)
    looked_up_field = data_fixture.create_date_field(
        name="lookupfield",
        table=table2,
        date_include_time=False,
        date_format="US",
    )

    linkrowfield = FieldHandler().create_field(
        user,
        table,
        "link_row",
        name="linkrowfield",
        link_row_table=table2,
    )

    table2_model = table2.get_model(attribute_names=True)
    a = table2_model.objects.create(lookupfield=f"2021-02-01", primaryfield="primary a")
    b = table2_model.objects.create(lookupfield=f"2022-02-03", primaryfield="primary b")

    table_model = table.get_model(attribute_names=True)

    table_row = table_model.objects.create(p="table row 1")
    table_row.linkrowfield.add(a.id)
    table_row.linkrowfield.add(b.id)
    table_row.save()

    formulafield = FieldHandler().create_field(
        user,
        table,
        "formula",
        name="formulafield",
        formula=f"IF(datetime_format(lookup('{linkrowfield.name}',"
        f"'{looked_up_field.name}'), "
        f"'YYYY')='2021', 'yes', 'no')",
    )
    response = api_client.get(
        reverse("api:database:rows:list", kwargs={"table_id": table.id}),
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    assert response.json() == {
        "count": 1,
        "next": None,
        "previous": None,
        "results": [
            {
                f"field_{table_primary_field.id}": "table row 1",
                f"field_{linkrowfield.id}": [
                    {"id": a.id, "value": "primary a"},
                    {"id": b.id, "value": "primary b"},
                ],
                f"field_{formulafield.id}": [
                    {"value": "yes", "id": a.id},
                    {"value": "no", "id": b.id},
                ],
                "id": table_row.id,
                "order": "1.00000000000000000000",
            }
        ],
    }
    response = api_client.delete(
        reverse(
            "api:database:rows:item",
            kwargs={"table_id": table2.id, "row_id": a.id},
        ),
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    assert response.status_code == HTTP_204_NO_CONTENT
    response = api_client.get(
        reverse("api:database:rows:list", kwargs={"table_id": table.id}),
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    assert response.json() == {
        "count": 1,
        "next": None,
        "previous": None,
        "results": [
            {
                f"field_{table_primary_field.id}": "table row 1",
                f"field_{linkrowfield.id}": [
                    {"id": b.id, "value": "primary b"},
                ],
                f"field_{formulafield.id}": [
                    {"value": "no", "id": b.id},
                ],
                "id": table_row.id,
                "order": "1.00000000000000000000",
            }
        ],
    }


@pytest.mark.django_db
def test_can_delete_double_link_lookup_field_value(
    data_fixture, api_client, django_assert_num_queries
):
    user, token = data_fixture.create_user_and_token()
    table = data_fixture.create_database_table(user=user)
    table2 = data_fixture.create_database_table(user=user, database=table.database)
    table3 = data_fixture.create_database_table(user=user, database=table.database)
    table_primary_field = data_fixture.create_text_field(
        name="p", table=table, primary=True
    )
    data_fixture.create_text_field(name="primaryfield", table=table2, primary=True)
    data_fixture.create_text_field(name="primaryfield", table=table3, primary=True)
    table2_linkrowfield = FieldHandler().create_field(
        user,
        table2,
        "link_row",
        name="linkrowfield",
        link_row_table=table3,
    )
    table3_model = table3.get_model(attribute_names=True)
    table3_1 = table3_model.objects.create(primaryfield="table 3 row 1")
    table3_2 = table3_model.objects.create(primaryfield="table 3 row 2")

    linkrowfield = FieldHandler().create_field(
        user,
        table,
        "link_row",
        name="linkrowfield",
        link_row_table=table2,
    )

    table2_model = table2.get_model(attribute_names=True)
    table2_a = table2_model.objects.create(primaryfield="primary a")
    table2_a.linkrowfield.add(table3_1.id)
    table2_a.save()
    table2_b = table2_model.objects.create(primaryfield="primary b")
    table2_b.linkrowfield.add(table3_2.id)
    table2_b.save()

    table_model = table.get_model(attribute_names=True)

    table_row = table_model.objects.create(p="table row 1")
    table_row.linkrowfield.add(table2_a.id)
    table_row.linkrowfield.add(table2_b.id)
    table_row.save()

    formulafield = FieldHandler().create_field(
        user,
        table,
        "formula",
        name="formulafield",
        formula=f"lookup('{linkrowfield.name}','{table2_linkrowfield.name}')",
    )
    response = api_client.get(
        reverse("api:database:rows:list", kwargs={"table_id": table.id}),
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    assert response.json() == {
        "count": 1,
        "next": None,
        "previous": None,
        "results": [
            {
                f"field_{table_primary_field.id}": "table row 1",
                f"field_{linkrowfield.id}": [
                    {"id": table2_a.id, "value": "primary a"},
                    {"id": table2_b.id, "value": "primary b"},
                ],
                f"field_{formulafield.id}": [
                    {
                        "value": table3_1.primaryfield,
                        "ids": {
                            f"database_table_{table2.id}": table2_a.id,
                            f"database_table_{table3.id}": table3_1.id,
                        },
                    },
                    {
                        "value": table3_2.primaryfield,
                        "ids": {
                            f"database_table_{table2.id}": table2_b.id,
                            f"database_table_{table3.id}": table3_2.id,
                        },
                    },
                ],
                "id": table_row.id,
                "order": "1.00000000000000000000",
            }
        ],
    }
    response = api_client.delete(
        reverse(
            "api:database:rows:item",
            kwargs={"table_id": table2.id, "row_id": table2_a.id},
        ),
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    assert response.status_code == HTTP_204_NO_CONTENT
    response = api_client.get(
        reverse("api:database:rows:list", kwargs={"table_id": table.id}),
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    assert response.json() == {
        "count": 1,
        "next": None,
        "previous": None,
        "results": [
            {
                f"field_{table_primary_field.id}": "table row 1",
                f"field_{linkrowfield.id}": [
                    {"id": table2_b.id, "value": "primary b"},
                ],
                f"field_{formulafield.id}": [
                    {
                        "value": table3_2.primaryfield,
                        "ids": {
                            f"database_table_{table2.id}": table2_b.id,
                            f"database_table_{table3.id}": table3_2.id,
                        },
                    },
                ],
                "id": table_row.id,
                "order": "1.00000000000000000000",
            }
        ],
    }

    response = api_client.delete(
        reverse(
            "api:database:rows:item",
            kwargs={"table_id": table3.id, "row_id": table3_2.id},
        ),
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    assert response.status_code == HTTP_204_NO_CONTENT
    response = api_client.get(
        reverse("api:database:rows:list", kwargs={"table_id": table.id}),
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    assert response.json() == {
        "count": 1,
        "next": None,
        "previous": None,
        "results": [
            {
                f"field_{table_primary_field.id}": "table row 1",
                f"field_{linkrowfield.id}": [
                    {"id": table2_b.id, "value": "primary b"},
                ],
                f"field_{formulafield.id}": [],
                "id": table_row.id,
                "order": "1.00000000000000000000",
            }
        ],
    }


@pytest.mark.django_db
def test_all_functions_are_registered():
    def get_all_subclasses(cls):
        all_subclasses = []

        for subclass in cls.__subclasses__():
            if not inspect.isabstract(subclass):
                all_subclasses.append(subclass)
            all_subclasses.extend(get_all_subclasses(subclass))

        return all_subclasses

    funcs = formula_function_registry.get_all()
    names = [f.type for f in funcs]
    assert len(names) == len(get_all_subclasses(BaserowFunctionDefinition))
    # print(json.dumps(names, indent=4))


@pytest.mark.django_db
def test_row_dependency_update_functions_do_no_row_updates_for_same_table(
    data_fixture, django_assert_num_queries
):
    user = data_fixture.create_user()
    table = data_fixture.create_database_table(user=user)
    handler = FieldHandler()
    handler.create_field(user=user, table=table, type_name="text", name="a")
    # noinspection PyTypeChecker
    formula_field: FormulaField = handler.create_field(
        user=user,
        table=table,
        type_name="formula",
        name="formula",
        formula="field('a')",
    )
    table_model = table.get_model()
    row = table_model.objects.create()
    formula_field_type = FormulaFieldType()
    update_collector = FieldUpdateCollector(table)
    field_cache = FieldCache()
    field_cache.cache_model(table_model)

    formula_field_type.row_of_dependency_updated(
        formula_field, row, update_collector, field_cache, None
    )
    formula_field_type.row_of_dependency_updated(
        formula_field, row, update_collector, field_cache, []
    )
    formula_field_type.row_of_dependency_created(
        formula_field, row, update_collector, field_cache, None
    )
    formula_field_type.row_of_dependency_created(
        formula_field, row, update_collector, field_cache, []
    )
    formula_field_type.row_of_dependency_deleted(
        formula_field, row, update_collector, field_cache, None
    )
    formula_field_type.row_of_dependency_deleted(
        formula_field, row, update_collector, field_cache, []
    )
    with django_assert_num_queries(0):
        update_collector.apply_updates_and_get_updated_fields(field_cache)


@pytest.mark.django_db
def test_recalculated_internal_type_with_incorrect_syntax_formula_sets_to_invalid(
    data_fixture,
):
    user = data_fixture.create_user()
    table = data_fixture.create_database_table(user=user)
    handler = FieldHandler()
    handler.create_field(user=user, table=table, type_name="text", name="a")
    formula_field = handler.create_field(
        user=user,
        table=table,
        type_name="formula",
        name="formula",
        formula="field('a')",
    )
    formula_field.formula = "invalid"
    formula_field.save()
    assert formula_field.formula_type == BaserowFormulaInvalidType.type
    assert "Invalid syntax" in formula_field.error


@pytest.mark.django_db
def test_accessing_cached_internal_formula_second_time_does_no_queries(
    data_fixture, django_assert_num_queries
):
    user = data_fixture.create_user()
    table = data_fixture.create_database_table(user=user)
    handler = FieldHandler()
    a_field = handler.create_field(user=user, table=table, type_name="text", name="a")
    formula_field = handler.create_field(
        user=user,
        table=table,
        type_name="formula",
        name="formula",
        formula="field('a')",
    )
    with django_assert_num_queries(0):
        assert str(formula_field.cached_untyped_expression) == formula_field.formula
        assert (
            str(formula_field.cached_typed_internal_expression)
            == f"error_to_null(field('{a_field.db_column}'))"
        )
        assert formula_field.cached_formula_type.type == BaserowFormulaTextType.type


@pytest.mark.django_db
def test_saving_after_properties_have_been_cached_does_recalculation(data_fixture):
    user = data_fixture.create_user()
    table = data_fixture.create_database_table(user=user)
    handler = FieldHandler()
    a_field = handler.create_field(user=user, table=table, type_name="text", name="a")
    formula_field = handler.create_field(
        user=user,
        table=table,
        type_name="formula",
        name="formula",
        formula="field('a')",
    )
    assert str(formula_field.cached_untyped_expression) == formula_field.formula
    assert (
        str(formula_field.cached_typed_internal_expression)
        == f"error_to_null(field('{a_field.db_column}'))"
    )
    assert formula_field.cached_formula_type.type == BaserowFormulaTextType.type

    formula_field.formula = "1"
    formula_field.save()

    assert str(formula_field.cached_untyped_expression) == "1"
    assert str(formula_field.cached_typed_internal_expression) == f"error_to_nan(1)"
    assert formula_field.cached_formula_type.type == BaserowFormulaNumberType.type


@pytest.mark.django_db
def test_renaming_dependency_maintains_dependency_link(data_fixture):
    user = data_fixture.create_user()
    table = data_fixture.create_database_table(user=user)
    handler = FieldHandler()
    a_field = handler.create_field(user=user, table=table, type_name="text", name="a")
    formula_field = handler.create_field(
        user=user,
        table=table,
        type_name="formula",
        name="formula",
        formula="field('a')",
    )
    starting_dep = formula_field.dependencies.get()
    assert formula_field.field_dependencies.get().id == a_field.id
    assert starting_dep.broken_reference_field_name is None
    assert starting_dep.dependency_id == a_field.id
    handler.update_field(user, a_field, name="other")

    formula_field.refresh_from_db()
    assert formula_field.dependencies.get().id == starting_dep.id
    assert formula_field.field_dependencies.get().id == a_field.id
    assert formula_field.formula == "field('other')"


@pytest.mark.django_db
def test_can_insert_and_update_rows_with_formula_referencing_single_select(
    data_fixture,
):
    user = data_fixture.create_user()
    table = data_fixture.create_database_table(user=user)
    handler = FieldHandler()
    option_field = data_fixture.create_single_select_field(
        table=table, name="option_field", order=1
    )
    option_a = data_fixture.create_select_option(
        field=option_field, value="A", color="blue"
    )
    option_b = data_fixture.create_select_option(
        field=option_field, value="B", color="red"
    )
    formula_field = handler.create_field(
        user=user,
        table=table,
        type_name="formula",
        name="2",
        formula="field('option_field')",
    )

    row = RowHandler().create_row(
        user=user, table=table, values={f"field_{option_field.id}": option_a.id}
    )
    row.refresh_from_db()
    result = getattr(row, f"field_{formula_field.id}")
    assert result == {
        "id": option_a.id,
        "color": option_a.color,
        "value": option_a.value,
    }

    row = RowHandler().update_row_by_id(
        user=user,
        table=table,
        row_id=row.id,
        values={f"field_{option_field.id}": option_b.id},
    )
    row.refresh_from_db()
    result = getattr(row, f"field_{formula_field.id}")
    assert result == {
        "id": option_b.id,
        "color": option_b.color,
        "value": option_b.value,
    }

    row = RowHandler().create_row(user=user, table=table, values={})
    row.refresh_from_db()
    result = getattr(row, f"field_{formula_field.id}")
    assert result is None


@pytest.mark.django_db
def test_cannot_create_view_filter_or_sort_on_invalid_field(data_fixture):
    user = data_fixture.create_user()
    table, other_table, link = data_fixture.create_two_linked_tables(user=user)
    grid_view = data_fixture.create_grid_view(user, table=table)
    first_formula_field = FieldHandler().create_field(
        user, table, "formula", formula="1", name="source"
    )
    broken_formula_field = FieldHandler().create_field(
        user, table, "formula", formula="field('source')", name="a"
    )
    FieldHandler().delete_field(user, first_formula_field)

    option_field = data_fixture.create_single_select_field(
        table=table, name="option_field", order=1
    )
    data_fixture.create_select_option(field=option_field, value="A", color="blue")
    data_fixture.create_select_option(field=option_field, value="B", color="red")
    single_select_formula_field = FieldHandler().create_field(
        user=user,
        table=table,
        type_name="formula",
        name="2",
        formula="field('option_field')",
    )
    lookup_field = FieldHandler().create_field(
        user=user,
        table=table,
        type_name="lookup",
        name="lookup",
        through_field_name=link.name,
        target_field_name="primary",
    )

    broken_formula_field = FormulaField.objects.get(id=broken_formula_field.id)
    single_select_formula_field = FormulaField.objects.get(
        id=single_select_formula_field.id
    )
    lookup_field = LookupField.objects.get(id=lookup_field.id)
    assert broken_formula_field.formula_type == "invalid"
    assert single_select_formula_field.formula_type == "single_select"
    assert lookup_field.formula_type == "array"

    fields_which_cant_yet_be_sorted_or_filtered = [
        broken_formula_field,
        single_select_formula_field,
        lookup_field,
    ]
    for field in fields_which_cant_yet_be_sorted_or_filtered:
        for view_filter_type in view_filter_type_registry.get_all():
            with pytest.raises(ViewFilterTypeNotAllowedForField):
                ViewHandler().create_filter(
                    user,
                    grid_view,
                    field,
                    view_filter_type.type,
                    "",
                )

    for field in fields_which_cant_yet_be_sorted_or_filtered:
        with pytest.raises(ViewSortFieldNotSupported):
            ViewHandler().create_sort(user, grid_view, field, SORT_ORDER_ASC)

        with pytest.raises(ViewSortFieldNotSupported):
            ViewHandler().create_sort(user, grid_view, field, SORT_ORDER_DESC)


@pytest.mark.django_db
def test_can_cache_and_uncache_formula_model_field(
    data_fixture,
):
    user = data_fixture.create_user()
    table = data_fixture.create_database_table(user=user)
    handler = FieldHandler()
    formula_field = handler.create_field(
        user=user,
        table=table,
        type_name="formula",
        name="2",
        formula="'a'",
    )
    formula_field_type = field_type_registry.get_by_model(formula_field)
    formula_model_field = formula_field_type.get_model_field(formula_field)
    generated_models_cache.set("test_formula_key", formula_model_field)
    uncached = generated_models_cache.get("test_formula_key")
    assert uncached == formula_model_field
    assert isinstance(uncached, BaserowExpressionField)
    assert uncached.__class__ == TextField
    assert str(uncached.expression) == str(formula_model_field.expression)


@pytest.mark.django_db
def test_inserting_a_row_with_lookup_field_immediately_populates_it_with_empty_list(
    data_fixture,
):
    user = data_fixture.create_user()
    table_a, table_b, link_field = data_fixture.create_two_linked_tables(user=user)

    target_field = data_fixture.create_text_field(name="target", table=table_b)
    table_a_model = table_a.get_model(attribute_names=True)
    table_b_model = table_b.get_model(attribute_names=True)
    row_1 = table_b_model.objects.create(primary="1", target="target 1")
    row_2 = table_b_model.objects.create(primary="2", target="target 2")

    row_a = table_a_model.objects.create(primary="a")
    row_a.link.add(row_1.id)
    row_a.link.add(row_2.id)
    row_a.save()

    lookup = FieldHandler().create_field(
        user,
        table_a,
        "lookup",
        name="lookup",
        through_field_name="link",
        target_field_name="target",
    )
    model_with_lookup = table_a.get_model()
    inserted_row = model_with_lookup.objects.create()
    default_empty_value_for_lookup = getattr(inserted_row, f"field_{lookup.id}")
    assert default_empty_value_for_lookup is not None
    assert default_empty_value_for_lookup == "[]"


@pytest.mark.django_db
def test_multiple_formula_fields_with_different_django_lookups_being_used_to_filter(
    data_fixture, api_client, django_assert_num_queries
):
    user, token = data_fixture.create_user_and_token()

    table = data_fixture.create_database_table(user=user)

    grid_view = data_fixture.create_grid_view(user=user, table=table)
    option_field = data_fixture.create_single_select_field(user=user, table=table)

    formula_field_ref_link_field = FieldHandler().create_field(
        user=user, table=table, name="a", type_name="formula", formula=f"1"
    )
    formula_referencing_single_select = FieldHandler().create_field(
        user=user,
        table=table,
        type_name="formula",
        formula=f"field('{option_field.name}')",
        name="single_select_formula",
    )

    data_fixture.create_view_filter(
        user=user,
        view=grid_view,
        field=formula_field_ref_link_field,
        type="empty",
    )

    response = api_client.get(
        reverse("api:database:views:grid:list", kwargs={"view_id": grid_view.id}),
        format="json",
        HTTP_AUTHORIZATION=f"JWT {token}",
    )
    assert response.status_code == HTTP_200_OK


@pytest.mark.django_db
def test_cant_create_primary_lookup_that_looksup_itself(data_fixture):
    user = data_fixture.create_user()
    table_a, table_b, link_field = data_fixture.create_two_linked_tables(user=user)

    with pytest.raises(
        InvalidFormulaType,
        match="references itself via a link field causing a circular dependency",
    ):
        FieldHandler().update_field(
            user,
            table_a.field_set.get(primary=True).specific,
            new_type_name="formula",
            name="formulafield",
            formula=f"lookup('{link_field.name}', '{link_field.link_row_related_field.name}')",
        )


@pytest.mark.django_db(transaction=True)
def test_converting_link_row_field_with_formula_dependency(
    data_fixture, django_assert_num_queries
):
    user = data_fixture.create_user()
    table_a, table_b, link_field = data_fixture.create_two_linked_tables(user=user)

    table_b_primary_field = table_b.field_set.get(primary=True)
    table_a_primary_field = table_a.field_set.get(primary=True)
    lookup_of_table_b_pk = FieldHandler().create_field(
        user,
        table=table_a,
        type_name="formula",
        name="broken",
        formula=f"lookup('{link_field.name}','{table_b_primary_field.name}')",
    )
    # Change the link field to a text field, this should break the lookup formula
    with transaction.atomic():
        FieldHandler().update_field(user, link_field, new_type_name="text")

    lookup_of_table_b_pk.refresh_from_db()
    assert lookup_of_table_b_pk.formula_type == "invalid"
    assert (
        lookup_of_table_b_pk.error
        == "first lookup function argument must be a link row field"
    )


@pytest.mark.django_db(transaction=True)
def test_converted_reversed_link_row_field_with_formula_dependency(data_fixture):
    user = data_fixture.create_user()
    table_a, table_b, link_field = data_fixture.create_two_linked_tables(user=user)

    table_b_primary_field = table_b.field_set.get(primary=True)
    table_a_primary_field = table_a.field_set.get(primary=True)
    related_link_row_field = link_field.link_row_related_field
    lookup_of_table_b_pk = FieldHandler().create_field(
        user,
        table=table_a,
        type_name="formula",
        name="broken",
        formula=f"lookup('{link_field.name}','{table_b_primary_field.name}')",
    )
    # Change the link field to a text field, this should break the lookup formula
    with transaction.atomic():
        FieldHandler().update_field(user, related_link_row_field, new_type_name="text")

    lookup_of_table_b_pk.refresh_from_db()
    assert lookup_of_table_b_pk.formula_type == "invalid"
    assert lookup_of_table_b_pk.error == "references the deleted or unknown field link"


def can_query_grid_view_for(api_client, grid_view, token):
    url = reverse("api:database:views:grid:list", kwargs={"view_id": grid_view.id})
    response = api_client.get(url, **{"HTTP_AUTHORIZATION": f"JWT {token}"})
    assert response.status_code == HTTP_200_OK
    response_json = response.json()
    assert len(response_json["results"]) == 10


@pytest.mark.django_db(transaction=True)
def test_every_formula_sub_type_can_be_a_primary_field(data_fixture, api_client):
    user, token = data_fixture.create_user_and_token()
    table_a, table_b, link_field = data_fixture.create_two_linked_tables(user=user)
    grid_view_table_a = data_fixture.create_grid_view(user, table=table_a)
    grid_view_table_b = data_fixture.create_grid_view(user, table=table_b)

    table_b_primary_field = table_b.field_set.get(primary=True)
    table_a_primary_field = table_a.field_set.get(primary=True).specific
    related_link_row_field = link_field.link_row_related_field

    data_fixture.create_email_field(table=table_a, name="email")
    option_field_table_a = data_fixture.create_single_select_field(
        table=table_a, name="single_select"
    )
    data_fixture.create_select_option(
        field=option_field_table_a, value="A", color="blue"
    )
    data_fixture.create_select_option(
        field=option_field_table_a, value="B", color="red"
    )

    data_fixture.create_text_field(table=table_b, name="text")
    data_fixture.create_number_field(table=table_b, name="int", number_decimal_places=0)
    data_fixture.create_boolean_field(table=table_b, name="bool")
    data_fixture.create_number_field(
        table=table_b, name="decimal", number_decimal_places=10
    )
    data_fixture.create_formula_field(
        table=table_b, name="date_interval", formula='date_interval("1 day")'
    )
    data_fixture.create_date_field(table=table_b, name="date")
    option_field_table_b = data_fixture.create_single_select_field(
        table=table_b, name="single_select"
    )
    data_fixture.create_select_option(
        field=option_field_table_b, value="A", color="blue"
    )
    data_fixture.create_select_option(
        field=option_field_table_b, value="B", color="red"
    )
    data_fixture.create_email_field(table=table_b, name="email")

    fill_table_rows(10, table_a)
    fill_table_rows(10, table_b)

    every_formula_type = [
        "CONCAT('test ', UPPER('formula'))",
        "1",
        "true",
        "100/3",
        "date_interval('1 day')",
        "todate('20200101', 'YYYYMMDD')",
        "field('single_select')",
        "field('email')",
        "1/0",
        f"lookup('{link_field.name}', 'text')",
        f"lookup('{link_field.name}', 'int')",
        f"lookup('{link_field.name}', 'bool')",
        f"lookup('{link_field.name}', 'decimal')",
        f"lookup('{link_field.name}', 'date_interval')",
        f"lookup('{link_field.name}', 'date')",
        f"lookup('{link_field.name}', 'single_select')",
        f"lookup('{link_field.name}', 'email')",
    ]
    for f in every_formula_type:
        primary_formula = FieldHandler().update_field(
            user,
            table_a_primary_field,
            new_type_name="formula",
            name="primary formula",
            formula=f,
        )
        assert primary_formula.error is None
        can_query_grid_view_for(api_client, grid_view_table_a, token)
        can_query_grid_view_for(api_client, grid_view_table_b, token)


@pytest.mark.django_db
def test_can_have_nested_date_formulas(
    data_fixture,
):
    user = data_fixture.create_user()
    table = data_fixture.create_database_table(user=user)
    data_fixture.create_date_field(table=table, name="jaar_van")

    FieldHandler().create_field(
        user,
        table,
        "formula",
        name="datum",
        formula="todate(concat(totext(year(field('jaar_van'))),'-01-01'),'YYYY-MM-DD')",
    )
    FieldHandler().create_field(
        user,
        table,
        "formula",
        name="failured",
        formula="date_diff('day', field('jaar_van'), field('datum')) + 1",
    )
