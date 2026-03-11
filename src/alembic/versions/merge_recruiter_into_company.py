"""merge_recruiter_into_company

Revision ID: merge_recruiter_into_company
Revises: 462c07477351
Create Date: 2025-03-11 23:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'merge_recruiter_into_company'
down_revision = '462c07477351'
branch_labels = None
depends_on = None


def upgrade():
    # Add new columns to companies table
    op.add_column('companies', sa.Column('email', sa.String(length=255), nullable=False))
    op.add_column('companies', sa.Column('password_hash', sa.String(length=255), nullable=False))
    op.add_column('companies', sa.Column('first_name', sa.String(length=100), nullable=False))
    op.add_column('companies', sa.Column('last_name', sa.String(length=100), nullable=False))
    op.add_column('companies', sa.Column('phone', sa.String(length=30), nullable=True))
    op.add_column('companies', sa.Column('profile_picture', sa.String(length=500), nullable=True))
    op.add_column('companies', sa.Column('role', sa.String(length=50), nullable=True))
    op.add_column('companies', sa.Column('bio', sa.Text(), nullable=True))
    op.add_column('companies', sa.Column('last_login', sa.DateTime(), nullable=True))
    
    # Add unique constraint on email
    op.create_unique_constraint('uq_companies_email', 'companies', ['email'])
    
    # Migrate data from recruiters to companies
    op.execute("""
        UPDATE companies 
        SET 
            email = r.email,
            password_hash = r.password_hash,
            first_name = r.first_name,
            last_name = r.last_name,
            phone = r.phone,
            profile_picture = r.profile_picture,
            role = r.role,
            bio = r.bio,
            last_login = r.last_login
        FROM recruiters r
        WHERE companies.company_id = r.company_id
    """)
    
    # Drop recruiter_id foreign key from job_requisitions
    op.drop_constraint('job_requisitions_recruiter_id_fkey', 'job_requisitions', type_='foreignkey')
    op.drop_column('job_requisitions', 'recruiter_id')
    
    # Drop recruiters table
    op.drop_table('recruiters')


def downgrade():
    # Create recruiters table
    op.create_table('recruiters',
        sa.Column('recruiter_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('first_name', sa.String(length=100), nullable=False),
        sa.Column('last_name', sa.String(length=100), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('role', sa.String(length=50), nullable=True),
        sa.Column('bio', sa.Text(), nullable=True),
        sa.Column('phone', sa.String(length=30), nullable=True),
        sa.Column('profile_picture', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
        sa.Column('last_login', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['companies.company_id'], ),
        sa.PrimaryKeyConstraint('recruiter_id'),
        sa.UniqueConstraint('email')
    )
    
    # Add recruiter_id to job_requisitions
    op.add_column('job_requisitions', sa.Column('recruiter_id', sa.Integer(), nullable=False))
    op.create_foreign_key('job_requisitions_recruiter_id_fkey', 'job_requisitions', 'recruiters', ['recruiter_id'], ['recruiter_id'])
    
    # Migrate data back to recruiters (this is a simplified migration - in practice you'd need to handle this more carefully)
    op.execute("""
        INSERT INTO recruiters (company_id, first_name, last_name, email, password_hash, phone, profile_picture, role, bio, last_login, created_at)
        SELECT 
            company_id, 
            first_name, 
            last_name, 
            email, 
            password_hash, 
            phone, 
            profile_picture, 
            role, 
            bio, 
            last_login, 
            created_at
        FROM companies
    """)
    
    # Update job_requisitions to point to the new recruiter records
    op.execute("""
        UPDATE job_requisitions 
        SET recruiter_id = r.recruiter_id
        FROM recruiters r
        WHERE job_requisitions.company_id = r.company_id
    """)
    
    # Drop columns from companies
    op.drop_constraint('uq_companies_email', 'companies', type_='unique')
    op.drop_column('companies', 'last_login')
    op.drop_column('companies', 'bio')
    op.drop_column('companies', 'role')
    op.drop_column('companies', 'profile_picture')
    op.drop_column('companies', 'phone')
    op.drop_column('companies', 'last_name')
    op.drop_column('companies', 'first_name')
    op.drop_column('companies', 'password_hash')
    op.drop_column('companies', 'email')
