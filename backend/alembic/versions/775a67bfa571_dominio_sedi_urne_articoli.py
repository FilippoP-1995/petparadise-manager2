"""dominio sedi urne articoli

Revision ID: 775a67bfa571
Revises: c1a3c3b110af
Create Date: 2026-08-26 23:39:31.895395

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '775a67bfa571'
down_revision = 'c1a3c3b110af'
branch_labels = None
depends_on = None

# doc06 'tabelle che restano concettualmente invariate': stesso set fisso
# di 6 nomi gia' seedato da V1 (app.py:661-662), stesso principio gia'
# usato per i 14 tag fissi (doc07).
_DEFAULT_ARTICLES = (
    "Sacchi per ritiro",
    "Boccette pelo",
    "Certificati",
    "Sacchetti riconsegna",
    "Sacchetti ceneri",
    "Cerniere e viti urne",
)


def upgrade() -> None:
    op.create_table(
        'articles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=150), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_articles_name'),
    )
    op.create_table(
        'urn_code_counters',
        sa.Column('key', sa.String(length=20), nullable=False),
        sa.Column('next_value', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )
    op.create_table(
        'article_orders',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('article_id', sa.Integer(), nullable=False),
        sa.Column('ordered_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['article_id'], ['articles.id'], name='fk_article_orders_article_id'),
        sa.ForeignKeyConstraint(['ordered_by'], ['users.id'], name='fk_article_orders_ordered_by'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'urn_movements',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('urn_id', sa.Integer(), nullable=False),
        sa.Column('practice_id', sa.Integer(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('movement_type', sa.String(length=50), nullable=False),
        sa.Column('quantity_delta', sa.Integer(), nullable=False),
        sa.Column('old_quantity', sa.Integer(), nullable=False),
        sa.Column('new_quantity', sa.Integer(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['practice_id'], ['practices.id'], name='fk_urn_movements_practice_id', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['urn_id'], ['urns.id'], name='fk_urn_movements_urn_id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_urn_movements_user_id'),
        sa.PrimaryKeyConstraint('id'),
    )

    op.add_column('company_locations', sa.Column('created_by', sa.Integer(), nullable=True))
    op.add_column('company_locations', sa.Column('updated_by', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_company_locations_created_by', 'company_locations', 'users', ['created_by'], ['id']
    )
    op.create_foreign_key(
        'fk_company_locations_updated_by', 'company_locations', 'users', ['updated_by'], ['id']
    )

    # ADD COLUMN su una tabella gia' esistente non crea da solo il tipo
    # enum Postgres (a differenza di CREATE TABLE) - va creato esplicitamente
    # prima, stesso principio gia' incontrato per pickup_type.
    urn_category_type = postgresql.ENUM('Urna', 'Accessorio', 'Calco', name='urn_category')
    urn_category_type.create(op.get_bind(), checkfirst=True)
    op.add_column('urns', sa.Column('category', urn_category_type, nullable=False, server_default='Urna'))
    op.add_column('urns', sa.Column('material', sa.String(length=100), nullable=True))
    op.add_column('urns', sa.Column('internal_code', sa.String(length=20), nullable=True))
    op.add_column('urns', sa.Column('price_cents', sa.BigInteger(), nullable=False, server_default='0'))
    op.add_column('urns', sa.Column('quantity', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('urns', sa.Column('low_stock_threshold', sa.Integer(), nullable=False, server_default='3'))
    op.add_column('urns', sa.Column('notes', sa.Text(), nullable=True))
    op.add_column('urns', sa.Column('created_by', sa.Integer(), nullable=True))
    op.add_column('urns', sa.Column('updated_by', sa.Integer(), nullable=True))
    # internal_code diventa NOT NULL solo dopo l'aggiunta (nessuna riga
    # esistente da backfillare oggi, ma la sequenza add->not null e' la
    # stessa usata altrove in questo progetto per colonne non derivabili
    # da un default statico).
    op.alter_column('urns', 'internal_code', nullable=False)
    op.create_unique_constraint('uq_urns_internal_code', 'urns', ['internal_code'])
    op.create_foreign_key('fk_urns_created_by', 'urns', 'users', ['created_by'], ['id'])
    op.create_foreign_key('fk_urns_updated_by', 'urns', 'users', ['updated_by'], ['id'])
    op.drop_column('urns', 'stock_quantity')
    # i server_default sopra servono solo per popolare in sicurezza le
    # colonne NOT NULL su una tabella gia' esistente - da qui in poi il
    # valore e' sempre scritto esplicitamente dall'applicazione (doc06
    # 'Convenzioni generali').
    op.alter_column('urns', 'category', server_default=None)
    op.alter_column('urns', 'price_cents', server_default=None)
    op.alter_column('urns', 'quantity', server_default=None)
    op.alter_column('urns', 'low_stock_threshold', server_default=None)

    articles_table = sa.table('articles', sa.column('name', sa.String), sa.column('active', sa.Boolean))
    op.bulk_insert(articles_table, [{"name": name, "active": True} for name in _DEFAULT_ARTICLES])


def downgrade() -> None:
    op.drop_constraint('fk_urns_updated_by', 'urns', type_='foreignkey')
    op.drop_constraint('fk_urns_created_by', 'urns', type_='foreignkey')
    op.drop_constraint('uq_urns_internal_code', 'urns', type_='unique')
    op.add_column('urns', sa.Column('stock_quantity', sa.Integer(), nullable=False, server_default='0'))
    op.alter_column('urns', 'stock_quantity', server_default=None)
    op.drop_column('urns', 'updated_by')
    op.drop_column('urns', 'created_by')
    op.drop_column('urns', 'notes')
    op.drop_column('urns', 'low_stock_threshold')
    op.drop_column('urns', 'quantity')
    op.drop_column('urns', 'price_cents')
    op.drop_column('urns', 'internal_code')
    op.drop_column('urns', 'category')
    op.execute('DROP TYPE IF EXISTS urn_category')

    op.drop_constraint('fk_company_locations_updated_by', 'company_locations', type_='foreignkey')
    op.drop_constraint('fk_company_locations_created_by', 'company_locations', type_='foreignkey')
    op.drop_column('company_locations', 'updated_by')
    op.drop_column('company_locations', 'created_by')

    op.drop_table('urn_movements')
    op.drop_table('article_orders')
    op.drop_table('urn_code_counters')
    op.drop_table('articles')
