from common.viewsets import CompanyScopedModelViewSet
from .models import Expense
from .serializers import ExpenseSerializer
class ExpenseViewSet(CompanyScopedModelViewSet):
    queryset=Expense.objects.all(); serializer_class=ExpenseSerializer
    def perform_create(self,serializer): serializer.save(company=self.request.user.company,branch=self.request.user.branch,created_by=self.request.user)
