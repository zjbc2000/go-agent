"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useRepositories } from "@/lib/providers/repository-context";
import { UserRoundPlus } from "lucide-react";
import { toast } from "sonner";

const employeeSchema = z.object({
  name: z.string().min(1, "姓名不能为空"),
  position: z.string().min(1, "职位不能为空"),
  prompt: z.string().min(1, "提示词不能为空"),
});

type EmployeeFormData = z.infer<typeof employeeSchema>;

interface NewEmployeeDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: () => void;
}

export function NewEmployeeDialog({ open, onOpenChange, onCreated }: NewEmployeeDialogProps) {
  const { company: companyRepo } = useRepositories();
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<EmployeeFormData>({
    resolver: zodResolver(employeeSchema),
    defaultValues: { name: "", position: "", prompt: "" },
  });

  const handleCreate = async (data: EmployeeFormData) => {
    try {
      await companyRepo.createEmployee(data);
      toast.success("员工已创建");
      reset();
      onOpenChange(false);
      onCreated();
    } catch {
      toast.error("创建失败，请重试");
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>新建员工</DialogTitle>
          <DialogDescription>填入姓名、职位和提示词，创建一个智能体员工</DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit(handleCreate)} className="space-y-3">
          <div>
            <Input {...register("name")} placeholder="姓名" className="h-8 text-sm" />
            {errors.name && <p className="text-xs text-destructive mt-1">{errors.name.message}</p>}
          </div>

          <div>
            <Input {...register("position")} placeholder="职位" className="h-8 text-sm" />
            {errors.position && (
              <p className="text-xs text-destructive mt-1">{errors.position.message}</p>
            )}
          </div>

          <div>
            <Textarea
              {...register("prompt")}
              placeholder="提示词（将注入到助手上下文）"
              rows={8}
              className="text-sm h-40 overflow-y-auto"
            />
            {errors.prompt && <p className="text-xs text-destructive mt-1">{errors.prompt.message}</p>}
          </div>

          <DialogFooter>
            <DialogClose render={<Button type="button" variant="outline">取消</Button>} />
            <Button type="submit" disabled={isSubmitting}>
              <UserRoundPlus className="h-3.5 w-3.5 mr-1" />
              {isSubmitting ? "创建中..." : "创建"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
