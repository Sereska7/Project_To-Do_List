from fastapi import HTTPException
from sqlalchemy import select, update, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.core.base.db_helper import db_helper as db
from app.core.exceptions.errors_task import TaskNotFound, NotOwnerError
from app.core.exceptions.general_errors import DataBaseError
from app.core.models.model_task import Task, TaskPermission, PermissionType
from app.core.schemas.schemas_task import TaskCreate, TaskRead
from app.core.schemas.schemas_user import UserRead
from app.core.exceptions.errors_user import UserHasNoPermission


async def task_create(
        task_in: TaskCreate,
        user: UserRead
) -> TaskRead:
    try:
        async with db.session_factory() as session:
            new_task = Task(
                name_task=task_in.name_task,
                description=task_in.description,
                date_from=task_in.date_from,
                date_to=task_in.date_to,
                user_id=user.id,
            )
            session.add(new_task)
            await session.commit()
            await session.refresh(new_task)
            return new_task
    except IntegrityError:
        await session.rollback()
        raise DataBaseError(f"Ошибка целостности данных.")


async def get_tasks() -> list[TaskRead]:
    async with db.session_factory() as session:
        tasks = await session.execute(select(Task.__table__.columns).order_by(Task.id))
        return tasks.mappings().all()


async def get_task_by_id(task_id: int):
    async with db.session_factory() as session:
        request = select(Task).where(Task.id == task_id)
        task = await session.execute(request)
        return task.one_or_none()


async def get_accessible_task(user_id: int, permission: PermissionType) -> list[TaskRead]:
    async with db.session_factory() as session:
        stmt = (
            select(Task)
            .distinct(Task.id)  # Удаляем дубликаты
            .outerjoin(TaskPermission, Task.id == TaskPermission.task_id)
            .filter(
                ((TaskPermission.user_id == user_id) & (TaskPermission.permission == permission.READ))
                | (Task.user_id == user_id)
            )
            .options(joinedload(Task.pr_task))  # Загружаем связанные данные
            .order_by(Task.id)
        )
        result = await session.execute(stmt)
        tasks = result.scalars().all()
        return tasks


async def update_task_with_permission_check(
    user_id: int,
    task_id: int,
    task_update: TaskCreate,
) -> TaskRead:
    async with db.session_factory() as session:
        # Проверяем, существует ли задача
        stmt = select(Task).filter(Task.id == task_id)
        result = await session.execute(stmt)
        task = result.scalars().one_or_none()

        if not task:
            raise TaskNotFound(f"Задача с id {task_id} не найдена.")

        # Проверяем права доступа пользователя к задаче
        stmt = (
            select(Task)
            .outerjoin(TaskPermission, Task.id == TaskPermission.task_id)
            .filter(
                ((TaskPermission.user_id == user_id)
                 & (TaskPermission.permission == PermissionType.UPDATE))
                | (Task.user_id == user_id)
            )
            .filter(Task.id == task_id)
        )
        result = await session.execute(stmt)
        accessible_task = result.scalars().one_or_none()

        if not accessible_task:
            raise UserHasNoPermission(f"У пользователя нет прав на обновление задачи с id {task_id}.")
        # Обновление задачи
        request = (
            update(Task)
            .where(Task.id == task_id)
            .values(
                name_task=task_update.name_task,
                description=task_update.description,
                date_from=task_update.date_from,
                date_to=task_update.date_to,
            )
            .execution_options(synchronize_session="fetch")
        )
        await session.execute(request)
        await session.commit()
        up_task = await session.execute(select(Task).where(Task.id == task_id))
        updated_task = up_task.scalars().one()
        return updated_task


async def delete_task_by_id(
        task_id: int,
        user_id: int
):
    """Удаляет задачу по её ID."""
    try:
        async with db.session_factory() as session:
            # Проверка наличия задачи
            task_stmt = select(Task).where(Task.id == task_id)
            task_result = await session.execute(task_stmt)
            task = task_result.scalars().first()

            if not task:
                raise TaskNotFound(f"Задача с id {task_id} не найдена")

            # Проверка прав доступа
            if task.user_id != user_id:
                raise NotOwnerError("Пользователь не является владельцем задачи.")

            # Выполнение запроса на удаление
            delete_stmt = delete(Task).where(Task.id == task_id)
            await session.execute(delete_stmt)
            await session.commit()

    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=500, detail="Ошибка целостности базы данных")

