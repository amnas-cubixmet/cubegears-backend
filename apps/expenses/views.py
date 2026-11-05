from common.viewsets import CompanyScopedModelViewSet
from .models import Expense
from .serializers import ExpenseSerializer
class ExpenseViewSet(CompanyScopedModelViewSet):
    queryset=Expense.objects.all(); serializer_class=ExpenseSerializer
    def perform_create(self,serializer): serializer.save(company=self.request.user.company,branch=self.request.user.branch,created_by=self.request.user)

    def perform_update(self, serializer):
        from rest_framework.exceptions import ValidationError
        if hasattr(serializer.instance, "supplier_credit_allocation"):
            raise ValidationError({"message": "Supplier settlements are immutable. Use a controlled reversal workflow."})
        serializer.save()

    def perform_destroy(self, instance):
        from rest_framework.exceptions import ValidationError
        if hasattr(instance, "supplier_credit_allocation"):
            raise ValidationError({"message": "Supplier settlements cannot be deleted from Expenses."})
        instance.delete()
