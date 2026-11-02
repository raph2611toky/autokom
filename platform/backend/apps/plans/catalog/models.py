import uuid

from django.db import models


class DeploymentModeOffer(models.Model):
    class Mode(models.TextChoices):
        DEVOPS = 'DEVOPS', 'DevOps'
        DEVSECOPS = 'DEVSECOPS', 'DevSecOps'
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=12, choices=Mode.choices, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    is_premium = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.name} ({"Premium" if self.is_premium else "Inclus"})'

    class Meta:
        db_table = 'plans_deploymentmodeoffer'


class ResourcePlan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    cpu_millicores = models.PositiveIntegerField(help_text='Limite CPU en milli-coeurs.')
    memory_mb = models.PositiveIntegerField(help_text='Mémoire RAM en Mo.')
    storage_mb = models.PositiveIntegerField(help_text='Stockage en Mo.')
    max_instances = models.PositiveIntegerField(default=1)
    is_premium = models.BooleanField(default=False)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.name} — {self.cpu_millicores}m CPU, {self.memory_mb} Mo'

    class Meta:
        db_table = 'plans_resourceplan'
        ordering = ['display_order', 'name']
        constraints = [
            models.CheckConstraint(check=models.Q(cpu_millicores__gt=0), name='positive_plan_cpu'),
            models.CheckConstraint(check=models.Q(memory_mb__gt=0), name='positive_plan_ram'),
            models.CheckConstraint(check=models.Q(storage_mb__gt=0), name='positive_plan_storage'),
        ]
